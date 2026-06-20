# reconcile.py — F5b: Stateful FSM-Gated Decision Engine
#
# Runs a diagnostic DAG over confidence scores, gates every decision through
# the ContainerFSM, enforces reversibility constraints, and supports
# both real and shadow (dry-run) execution modes.
#
# Key differences from stateless/reconcile.py:
#   1. Accepts a ContainerFSM instance — all decisions are FSM-gated.
#   2. Reversibility gate — irreversible actions are always blocked before dispatch.
#   3. Decision dict is a backward-compatible extension of the base trace schema:
#      all base fields present + stateful fields (fsm_state, fsm_transition, …).
#   4. Shadow mode logs divergence between ideal and actual actuation paths.
#
# Signature: reconcile(state_store, fsm, shadow=False)
# The extra `fsm` parameter is stateful-specific; the stateless track omits it.

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))
from constants import (
    FLUSH_THRESHOLD, REPAIR_THRESHOLD, ESCALATE_THRESHOLD, DEGRADE_THRESHOLD,
)
from confidence import (
    score_all, IO_HIGH_LATENCY_US,
    IO_LATENCY_SCORE_MAX, MOUNT_LOSS_SCORE,
)
from actions import REVERSIBILITY, IRREVERSIBLE_ACTIONS
from fsm import ContainerFSM

# ── DAG fault-pattern names (populate 'dag_pattern' in decision trace) ────────
_PATTERN = {
    "nominal":                 "nominal — score below all thresholds",
    "io_degradation_minor":    "minor I/O degradation; queue flush recommended",
    "io_degradation_major":    "severe I/O degradation; checkpoint-restart required",
    "io_degradation_critical": "critical I/O or mount failure; escalation required",
}


def _kernel_signals(failures: list) -> list:
    """Extract human-readable kernel signal names from raw failure list."""
    signals = set()
    for e in failures:
        ev = e.get("event", "")
        if ev == "blk_io_latency":
            signals.add(
                "blk_io_latency_high" if e.get("latency_us", 0) >= IO_HIGH_LATENCY_US
                else "blk_io_latency_normal"
            )
        elif ev == "vfs_write_error":
            signals.add("vfs_write_error")
        elif ev == "vfs_read_error":
            signals.add("vfs_read_error")
        elif ev == "volume_mount_lost":
            signals.add("volume_mount_lost")
        elif ev == "volume_mount_restored":
            signals.add("volume_mount_restored")
    return sorted(signals)


def _decide(container: str, score: float, failures: list, fsm: ContainerFSM) -> dict:
    """
    FSM-gated diagnostic DAG for a single container.

    DAG edges:
      score < FLUSH_THRESHOLD                             → no_action
      FLUSH_THRESHOLD    <= score < REPAIR_THRESHOLD      → flush_io_queue
      REPAIR_THRESHOLD   <= score < ESCALATE_THRESHOLD    → checkpoint_and_restart
      score >= ESCALATE_THRESHOLD                         → escalate

    FSM gating (in order):
      1. "degrade" fired when score >= DEGRADE_THRESHOLD and container
         is in Healthy or Recovered (entering degraded territory).
      2. checkpoint_and_restart requires FSM to be in Audited; otherwise
         downgraded to flush_io_queue so the FSM advances to Audited first.
      3. flush_io_queue fires "audit" trigger (Degraded → Audited).
      4. checkpoint_and_restart (if still selected) fires "approve_repair"
         trigger (Audited → Repairing).
      5. escalate requires FSM to be in Degraded, Audited, or Repairing;
         otherwise downgraded to flush_io_queue.
      6. Heal: if score drops below FLUSH_THRESHOLD and FSM is Recovered,
         fire "heal" trigger (Recovered → Healthy).

    Reversibility gate (step 0): irreversible actions always blocked.
    """
    signals          = _kernel_signals(failures)
    fsm_state_before = fsm.state(container)
    fsm_transition   = None
    blocked_reason   = None

    # ── Step 0: Raw DAG decision ──────────────────────────────────────────────
    if score >= ESCALATE_THRESHOLD:
        raw_action  = "escalate"
        dag_pattern = "io_degradation_critical"
        reason      = f"score {score} >= escalate threshold {ESCALATE_THRESHOLD}"
    elif score >= REPAIR_THRESHOLD:
        raw_action  = "checkpoint_and_restart"
        dag_pattern = "io_degradation_major"
        reason      = f"score {score} >= repair threshold {REPAIR_THRESHOLD}"
    elif score >= FLUSH_THRESHOLD:
        raw_action  = "flush_io_queue"
        dag_pattern = "io_degradation_minor"
        reason      = f"score {score} >= flush threshold {FLUSH_THRESHOLD}"
    else:
        raw_action  = "no_action"
        dag_pattern = "nominal"
        reason      = f"score {score} below flush threshold {FLUSH_THRESHOLD}"

    action = raw_action

    # ── Step 1: Reversibility gate ────────────────────────────────────────────
    if raw_action in IRREVERSIBLE_ACTIONS:
        action         = "no_action"
        blocked_reason = f"{raw_action} is irreversible — blocked by safety gate"
        reason         = blocked_reason

    # ── Step 2: FSM degrade trigger ───────────────────────────────────────────
    # Only fire "degrade" when entering degraded territory from Healthy or Recovered.
    # If already Degraded / Audited / Repairing, the FSM is already tracking this.
    current = fsm.state(container)
    if score >= DEGRADE_THRESHOLD and action != "no_action":
        if current in ("Healthy", "Recovered"):
            t = fsm.apply(container, "degrade")
            if t:
                fsm_transition = f"{t[0]}→{t[1]}"
            current = fsm.state(container)

    # ── Step 3: FSM gate for checkpoint_and_restart ───────────────────────────
    # Checkpoint-restart is only safe from Audited state.
    # If FSM isn't there yet, downgrade to flush so it can advance to Audited first.
    if action == "checkpoint_and_restart" and current != "Audited":
        action         = "flush_io_queue"
        dag_pattern    = "io_degradation_minor"
        blocked_reason = (
            f"checkpoint_and_restart deferred: FSM is {current}, must reach Audited first"
        )
        reason = blocked_reason

    # ── Step 4: flush_io_queue fires audit transition ─────────────────────────
    if action == "flush_io_queue" and current == "Degraded":
        t = fsm.apply(container, "audit")
        if t:
            fsm_transition = f"{t[0]}→{t[1]}"
            current = fsm.state(container)

    # ── Step 5: checkpoint_and_restart fires approve_repair ───────────────────
    if action == "checkpoint_and_restart" and current == "Audited":
        t = fsm.apply(container, "approve_repair")
        if t:
            fsm_transition = f"{t[0]}→{t[1]}"
            current = fsm.state(container)

    # ── Step 6: escalate FSM gate ─────────────────────────────────────────────
    # Escalate is only dispatched once the FSM has entered degraded territory.
    if action == "escalate" and current not in ("Degraded", "Audited", "Repairing"):
        action         = "flush_io_queue"
        blocked_reason = f"escalate blocked: FSM is {current}; need Degraded/Audited/Repairing"
        reason         = blocked_reason

    # ── Step 7: Heal from Recovered when score drops ──────────────────────────
    if score < FLUSH_THRESHOLD and fsm.state(container) == "Recovered":
        t = fsm.apply(container, "heal")
        if t:
            fsm_transition = f"{t[0]}→{t[1]}"

    return {
        # Base fields (compatible with stateless/reconcile.py decision dict)
        "container":       container,
        "score":           score,
        "action":          action,
        "reason":          reason,
        "timestamp":       time.time(),
        # Stateful extension fields
        "fsm_state":       fsm_state_before,
        "fsm_state_after": fsm.state(container),   # state after this cycle's transitions
        "fsm_transition":  fsm_transition,
        "reversibility":   REVERSIBILITY.get(action, "unknown"),
        "kernel_signals":  signals,
        "dag_pattern":     dag_pattern,
        "blocked_reason":  blocked_reason,
    }


def reconcile(state_store, fsm: ContainerFSM, shadow: bool = False) -> list:
    """
    Run the FSM-gated stateful decision engine over all tracked containers.

    state_store — StatefulStateStore instance
    fsm         — ContainerFSM instance (mutated in-place on the real path)
    shadow      — if True, decisions are computed but execution is suppressed;
                  divergence between shadow and real paths is logged by main.py

    Returns list of decision dicts with 'mode' field set.
    """
    scores    = score_all(state_store)
    decisions = []

    for container, score in scores.items():
        failures = state_store.get_failures(container)
        decision = _decide(container, score, failures, fsm)
        decision["mode"] = "shadow" if shadow else "real"
        decisions.append(decision)

        tag = "[SHADOW-F]" if shadow else "[REAL-F]"
        print(
            f"{tag} container={container} score={score} "
            f"fsm={decision['fsm_state']} "
            f"→ {decision['action']}  ({decision['reason']})",
            flush=True,
        )

    return decisions


def reconcile_both(state_store, fsm: ContainerFSM) -> dict:
    """
    Run both real and shadow paths and return both sets of decisions.

    Real path  — uses the live `fsm` (mutated in-place as normal).
    Shadow path — uses a FRESH throwaway ContainerFSM() so the live FSM is
                  never touched by the shadow computation.

    Returns {"real": [...], "shadow": [...]}.
    """
    print("--- Real path ---", flush=True)
    real = reconcile(state_store, fsm, shadow=False)
    print("--- Shadow path (throwaway FSM) ---", flush=True)
    shadow = reconcile(state_store, ContainerFSM(), shadow=True)
    return {"real": real, "shadow": shadow}


# --- Self-test ---
if __name__ == "__main__":
    from state_store import StatefulStateStore
    from fsm import ContainerFSM

    store = StatefulStateStore(window_seconds=60)
    fsm   = ContainerFSM()
    now   = time.time()

    # postgres: 5 high-latency events → score 0.85 → checkpoint_and_restart (but FSM gates
    # it to flush_io_queue on cycle 1 since it starts in Healthy, not Audited)
    for i in range(5):
        store.record({"container": "postgres", "pid": 100 + i,
                      "event": "blk_io_latency", "latency_us": 15_000,
                      "time": now, "node_type": "F"})

    # etcd: volume mount lost → score 0.97 → escalate
    store.record({"container": "etcd", "pid": 201,
                  "event": "volume_mount_lost", "volume_path": "/data",
                  "time": now, "node_type": "F"})

    # redis-persist: low score (1 VFS error → 0.293) → no_action
    store.record({"container": "redis-persist", "pid": 301,
                  "event": "vfs_write_error", "time": now, "node_type": "F"})

    decisions = reconcile(store, fsm, shadow=False)
    by_name   = {d["container"]: d for d in decisions}

    print("\n--- Cycle 1 decisions ---")
    for c, d in sorted(by_name.items()):
        print(f"  {c:<16} score={d['score']} fsm_before={d['fsm_state']}"
              f" transition={d['fsm_transition']} → {d['action']}")

    # postgres cycle 1: FSM was Healthy; degrade fires (→Degraded), then flush audit (→Audited)
    # checkpoint_and_restart deferred to flush_io_queue because FSM wasn't Audited at gate time
    assert by_name["postgres"]["action"] == "flush_io_queue", by_name["postgres"]
    assert by_name["postgres"]["fsm_state"] == "Healthy"

    # etcd: score 0.97 → escalate; degrade fires Healthy→Degraded; Degraded is allowed → escalate
    assert by_name["etcd"]["action"] == "escalate", by_name["etcd"]

    # redis-persist: score 0.293 < 0.50 → no_action
    assert by_name["redis-persist"]["action"] == "no_action", by_name["redis-persist"]

    # Reversibility gate: no irreversible action should ever appear
    for d in decisions:
        assert d["reversibility"] != "irreversible", f"Irreversible action leaked: {d}"

    # Cycle 2: postgres FSM is now Audited → checkpoint_and_restart should be allowed
    print("\n--- Cycle 2 decisions (postgres should get checkpoint_and_restart) ---")
    decisions2 = reconcile(store, fsm, shadow=False)
    by_name2   = {d["container"]: d for d in decisions2}
    print(f"  postgres: fsm_before={by_name2['postgres']['fsm_state']}"
          f" → {by_name2['postgres']['action']}")
    assert by_name2["postgres"]["action"] == "checkpoint_and_restart", by_name2["postgres"]

    print("\nAll self-tests passed.")
