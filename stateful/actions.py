# actions.py — F6: Stateful Action Executor
#
# Executes remediation decisions from stateful/reconcile.py.
# Every action carries a Reversibility tag (see REVERSIBILITY dict).
#
#   reversible   — safe to execute automatically; effect can be undone
#   conditional  — requires confidence threshold AND (production) human confirmation
#   irreversible — ALWAYS blocked by the reversibility gate in reconcile.py;
#                  never dispatched to execute()
#
# Action catalogue:
#   no_action              (reversible)   — no-op; confidence below threshold
#   flush_io_queue         (reversible)   — synchronous durable flush (CHECKPOINT /
#                                           SAVE / FLUSH TABLES) inside the container
#   checkpoint_and_restart (reversible)   — CRIU checkpoint then docker restore;
#                                           falls back to docker restart if CRIU fails
#   escalate               (conditional)  — page on-call; suppressed in shadow mode
#   volume_delete          (irreversible) — BLOCKED by gate; never reaches execute()

import collections
import math
import subprocess
import time

# ── WASM policy sandbox (optional — falls back gracefully if wasmtime missing) ─
try:
    from wasm_executor import policy_check as _wasm_policy_check
    _WASM_AVAILABLE = True
except ImportError:
    import warnings as _warnings
    _WASM_AVAILABLE = False
    _warnings.warn(
        "[WASM-FALLBACK] wasmtime is not installed. "
        "Using Python policy fallback — FSM constraints are still enforced. "
        "Install wasmtime (`pip install wasmtime`) for full sandbox isolation.",
        RuntimeWarning,
        stacklevel=2,
    )

    def _wasm_policy_check(action, fsm_state, score):  # noqa: E302
        """
        Python-level policy fallback used when wasmtime is unavailable.
        Matches stateful/wasm/*.wat gate logic; FSM constraints are still enforced.
        FSM encoding: Healthy=0  Degraded=1  Audited=2  Repairing=3  Recovered=4
        """
        _FSM_CODE = {
            "Healthy": 0, "Degraded": 1, "Audited": 2,
            "Repairing": 3, "Recovered": 4,
        }
        fsm_code = _FSM_CODE.get(fsm_state)
        if fsm_code is None:
            return False, f"Python fallback: unknown FSM state {fsm_state!r}"

        if action == "no_action":
            return True, "Python fallback: no_action always allowed"

        if action == "flush_io_queue":
            if score < 0.50:
                return False, f"Python fallback: flush_io_queue blocked — score {score:.3f} < 0.50"
            if fsm_code in (1, 2):   # Degraded or Audited
                return True, "Python fallback: policy satisfied"
            return False, (
                f"Python fallback: flush_io_queue blocked — "
                f"FSM={fsm_state!r} not in (Degraded, Audited)"
            )

        if action == "checkpoint_and_restart":
            if score < 0.80:
                return False, (
                    f"Python fallback: checkpoint_and_restart blocked — "
                    f"score {score:.3f} < 0.80"
                )
            # Allow Audited only — matches reconcile.py Step 3 and checkpoint_restart.wat.
            if fsm_code == 2:   # Audited
                return True, "Python fallback: policy satisfied"
            return False, (
                f"Python fallback: checkpoint_and_restart blocked — "
                f"FSM={fsm_state!r} not in (Audited)"
            )

        if action == "escalate":
            if score < 0.95:
                return False, f"Python fallback: escalate blocked — score {score:.3f} < 0.95"
            if fsm_code in (1, 2, 3):   # Degraded, Audited, or Repairing
                return True, "Python fallback: policy satisfied"
            return False, (
                f"Python fallback: escalate blocked — "
                f"FSM={fsm_state!r} not in (Degraded, Audited, Repairing)"
            )

        # Unknown action — allow (mirrors no_action.wat behaviour for unregistered actions)
        return True, f"Python fallback: unknown action {action!r} — defaulting to allow"

# ── WASM latency tracking ──────────────────────────────────────────────────────
# Rolling window of the last 10,000 per-call latency samples in nanoseconds.
_wasm_latency_ns: collections.deque = collections.deque(maxlen=10_000)


def wasm_latency_stats() -> dict:
    """
    Return mean/stddev/count of WASM (or Python fallback) policy check latency.
    Values are in microseconds.  Call at shutdown to report overhead figures.
    """
    n = len(_wasm_latency_ns)
    if n == 0:
        return {"count": 0, "mean_us": 0.0, "stddev_us": 0.0,
                "engine": "WASM" if _WASM_AVAILABLE else "Python-fallback"}
    mean = sum(_wasm_latency_ns) / n
    variance = sum((x - mean) ** 2 for x in _wasm_latency_ns) / n
    return {
        "count":     n,
        "mean_us":   round(mean / 1_000, 2),
        "stddev_us": round(math.sqrt(variance) / 1_000, 2),
        "engine":    "WASM" if _WASM_AVAILABLE else "Python-fallback",
    }


# ── Reversibility catalogue — imported by reconcile.py for gate enforcement ───
REVERSIBILITY: dict = {
    "no_action":              "reversible",
    "flush_io_queue":         "reversible",
    "checkpoint_and_restart": "reversible",
    "escalate":               "conditional",
    "volume_delete":          "irreversible",   # gate in reconcile.py always blocks this
}

IRREVERSIBLE_ACTIONS = {k for k, v in REVERSIBILITY.items() if v == "irreversible"}


def _result(container: str, action: str, success: bool,
            stdout: str = "", stderr: str = "") -> dict:
    return {
        "container":     container,
        "action":        action,
        "success":       success,
        "stdout":        stdout,
        "stderr":        stderr,
        "timestamp":     time.time(),
        "mode":          "real",
        "reversibility": REVERSIBILITY.get(action, "unknown"),
    }


def no_action(container: str) -> dict:
    """No-op — confidence below threshold."""
    print(f"[actions-f] no_action: {container}")
    return _result(container, "no_action", True)


# ── Durable-flush primitives, one per stateful workload ───────────────────────
# Each command is run *inside* the container via `docker exec` so the server
# process itself performs the write.  Every primitive is synchronous: it returns
# only after the workload reports the flush complete.
#
#   postgres  CHECKPOINT           — forces all dirty shared buffers to WAL+heap
#   redis     SAVE (not BGSAVE)    — synchronous RDB dump; blocks until fsync'd
#   mysql     FLUSH TABLES; FLUSH LOGS — closes/flushes table handles, rotates
#                                    and fsyncs binlog + redo
#
# Verification (see scripts/verify_flush.py):
#   postgres  pg_stat_checkpointer.num_requested          increments by 1
#   redis     INFO persistence → rdb_last_save_time        advances
#   mysql     Innodb_data_fsyncs increments; binlog file rotates
#             (no SQL statement synchronously writes the InnoDB buffer pool;
#              durability is the fsync'd redo log + binlog, which FLUSH LOGS forces)
_FLUSH_CMD = {
    "postgres": ["psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1",
                 "-c", "CHECKPOINT;"],
    "redis":    ["redis-cli", "SAVE"],
    "mysql":    ["mysql", "-uroot", "-ppass", "-e", "FLUSH TABLES; FLUSH LOGS;"],
}
_FLUSH_TIMEOUT_S = 60


def flush_io_queue(container: str) -> dict:
    """
    Force the workload to complete a durable flush of its in-memory state.

    Runs the workload's native flush primitive (see _FLUSH_CMD) inside the
    container.  The call is synchronous: success means the server itself has
    reported the flush finished, so any later checkpoint_and_restart operates
    on an on-disk state that is consistent with everything committed so far.

    Result dict extra fields:
        flush_latency_ms  (float) — wall time for the flush command
        flush_cmd         (str)   — the primitive that was executed
    """
    print(f"[actions-f] flush_io_queue: {container}")
    cmd = _FLUSH_CMD.get(container)
    if cmd is None:
        result = _result(
            container, "flush_io_queue", False,
            stderr=f"no durable-flush primitive registered for {container!r}",
        )
        result["flush_latency_ms"] = 0.0
        result["flush_cmd"] = ""
        return result

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["docker", "exec", container, *cmd],
            capture_output=True, text=True, timeout=_FLUSH_TIMEOUT_S,
        )
        ok      = proc.returncode == 0
        stdout  = proc.stdout.strip()
        # mysql prints a password-on-CLI warning on stderr even on success
        stderr  = "\n".join(
            l for l in proc.stderr.strip().splitlines()
            if "Using a password on the command line" not in l
        )
    except FileNotFoundError:
        ok, stdout, stderr = False, "", "docker not found"
    except subprocess.TimeoutExpired:
        ok, stdout, stderr = False, "", f"flush timed out after {_FLUSH_TIMEOUT_S}s"
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    print(
        f"[actions-f] flush_io_queue {container}: "
        f"{'ok' if ok else 'FAILED'} ({latency_ms}ms) {' '.join(cmd)!r}"
    )
    result = _result(
        container, "flush_io_queue", ok,
        stdout=f"{' '.join(cmd)} → {stdout or 'ok'} ({latency_ms}ms)",
        stderr=stderr,
    )
    result["flush_latency_ms"] = latency_ms
    result["flush_cmd"] = " ".join(cmd)
    return result


def checkpoint_and_restart(container: str) -> dict:
    """
    Checkpoint the container via CRIU (docker checkpoint create), then restore
    it (docker start --checkpoint).  Falls back to docker restart if CRIU is
    unavailable or the checkpoint fails.

    Result dict extra fields:
        checkpoint_success   (bool)  — True if CRIU checkpoint was created
        restore_success      (bool)  — True if container came back up
        checkpoint_latency_ms (float) — wall time for the checkpoint phase
        checkpoint_name      (str)   — name of the checkpoint artefact
        criu_used            (bool)  — True if CRIU path was taken (not fallback)
    """
    print(f"[actions-f] checkpoint_and_restart: {container}")
    ckpt_name = f"uc_{container}_{int(time.time())}"

    # ── Phase 1: CRIU checkpoint ───────────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        ckpt = subprocess.run(
            ["docker", "checkpoint", "create", container, ckpt_name],
            capture_output=True, text=True, timeout=60,
        )
        ckpt_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        ckpt_ok = ckpt.returncode == 0
    except FileNotFoundError:
        ckpt_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        ckpt_ok = False
        ckpt = type("R", (), {"stdout": "", "stderr": "docker not found"})()
    except subprocess.TimeoutExpired:
        ckpt_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        ckpt_ok = False
        ckpt = type("R", (), {"stdout": "", "stderr": "docker checkpoint timed out"})()

    if ckpt_ok:
        # ── Phase 2: restore from checkpoint ──────────────────────────────────
        try:
            restore = subprocess.run(
                ["docker", "start", "--checkpoint", ckpt_name, container],
                capture_output=True, text=True, timeout=60,
            )
            restore_ok = restore.returncode == 0
            restore_stderr = restore.stderr.strip()
            restore_stdout = restore.stdout.strip()
        except subprocess.TimeoutExpired:
            restore_ok = False
            restore_stderr = "docker start --checkpoint timed out"
            restore_stdout = ""

        print(
            f"[actions-f] CRIU checkpoint '{ckpt_name}' "
            f"({ckpt_latency_ms}ms) — restore {'OK' if restore_ok else 'FAILED'}"
        )

        if restore_ok:
            result = _result(
                container, "checkpoint_and_restart", True,
                stdout=(
                    f"CRIU checkpoint '{ckpt_name}' created in {ckpt_latency_ms}ms | "
                    f"restore succeeded: {restore_stdout}"
                ),
                stderr=restore_stderr,
            )
            result["checkpoint_success"]    = True
            result["restore_success"]       = True
            result["checkpoint_latency_ms"] = ckpt_latency_ms
            result["checkpoint_name"]       = ckpt_name
            result["criu_used"]             = True
            return result

        # Checkpoint created but restore failed (e.g. netns bind-mount on Linux 5.15)
        # Fall back to docker restart so the container actually recovers.
        print(
            f"[actions-f] CRIU restore failed: {restore_stderr[:100]} "
            f"— falling back to docker restart"
        )
        try:
            fallback = subprocess.run(
                ["docker", "restart", container],
                capture_output=True, text=True, timeout=30,
            )
            fallback_ok = fallback.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            fallback_ok = False
            fallback = type("R", (), {"stdout": "", "stderr": "docker restart failed"})()

        result = _result(
            container, "checkpoint_and_restart", fallback_ok,
            stdout=(
                f"CRIU checkpoint '{ckpt_name}' created ({ckpt_latency_ms}ms); "
                f"restore FAILED ({restore_stderr[:60]}); "
                f"docker restart {'succeeded' if fallback_ok else 'FAILED'}"
            ),
            stderr=restore_stderr,
        )
        result["checkpoint_success"]    = True
        result["restore_success"]       = False
        result["checkpoint_latency_ms"] = ckpt_latency_ms
        result["checkpoint_name"]       = ckpt_name
        result["criu_used"]             = True
        return result

    # ── Fallback: docker restart ───────────────────────────────────────────────
    criu_err = getattr(ckpt, "stderr", "").strip()
    print(
        f"[actions-f] CRIU checkpoint failed ({ckpt_latency_ms}ms): "
        f"{criu_err[:100]} — falling back to docker restart"
    )
    try:
        restart = subprocess.run(
            ["docker", "restart", container],
            capture_output=True, text=True, timeout=30,
        )
        restart_ok = restart.returncode == 0
        restart_stderr = restart.stderr.strip()
        restart_stdout = restart.stdout.strip()
    except FileNotFoundError:
        restart_ok = False
        restart_stderr = "docker not found"
        restart_stdout = ""
    except subprocess.TimeoutExpired:
        restart_ok = False
        restart_stderr = "docker restart timed out"
        restart_stdout = ""

    result = _result(
        container, "checkpoint_and_restart", restart_ok,
        stdout=(
            f"CRIU unavailable (checkpoint {ckpt_latency_ms}ms, err: {criu_err[:60]}); "
            f"docker restart {'succeeded' if restart_ok else 'FAILED'}: {restart_stdout}"
        ),
        stderr=f"checkpoint: {criu_err} | restart: {restart_stderr}",
    )
    result["checkpoint_success"]    = False
    result["restore_success"]       = restart_ok
    result["checkpoint_latency_ms"] = ckpt_latency_ms
    result["checkpoint_name"]       = ckpt_name
    result["criu_used"]             = False
    return result


def escalate(container: str) -> dict:
    """
    Escalate to on-call operator.
    Conditional: only dispatched when confidence >= ESCALATE_THRESHOLD
    and the reversibility gate permits it.
    In production this would page PagerDuty / OpsGenie.
    """
    print(f"[actions-f] ESCALATE: {container}")
    return _result(
        container, "escalate", True,
        stdout=f"Escalation logged for {container} — on-call alert dispatched (no live pager integration)",
    )


def execute(decision: dict) -> dict:
    """
    Dispatch a stateful decision dict to the correct action function.
    Shadow-mode decisions are logged but never executed.

    For real-mode decisions, the WASM sandbox policy_check() is called first.
    If the sandbox rejects the action (FSM-sequence violation or score below
    threshold), execution is suppressed and wasm_blocked=True is recorded.
    This is the second, independent gate described in the research paper.

    WASM call latency is accumulated in _wasm_latency_ns for overhead reporting.
    """
    container = decision["container"]
    action    = decision.get("action", "no_action")
    mode      = decision.get("mode", "real")

    if mode == "shadow":
        print(f"[actions-f][SHADOW] would execute '{action}' on {container} — skipping")
        return {
            "container":     container,
            "action":        action,
            "success":       None,
            "stdout":        f"SHADOW: would run {action}",
            "stderr":        "",
            "timestamp":     time.time(),
            "mode":          "shadow",
            "reversibility": REVERSIBILITY.get(action, "unknown"),
            "wasm_blocked":  False,
            "wasm_reason":   None,
        }

    # ── WASM sandbox check (real mode only) ───────────────────────────────────
    # Use fsm_state_after (state after Python-level FSM transitions) so the
    # WASM module sees the same state the Python gate used for its decision.
    fsm_state = decision.get("fsm_state_after", decision.get("fsm_state", "Healthy"))
    score     = float(decision.get("score", 0.0))

    _t0 = time.perf_counter_ns()
    wasm_ok, wasm_reason = _wasm_policy_check(action, fsm_state, score)
    _wasm_latency_ns.append(time.perf_counter_ns() - _t0)

    if not wasm_ok:
        print(f"[actions-f][WASM-BLOCKED] {container}: {wasm_reason}")
        result = _result(container, "no_action", False, stderr=wasm_reason)
        result["mode"]         = "real"
        result["wasm_blocked"] = True
        result["wasm_reason"]  = wasm_reason
        return result

    dispatch = {
        "no_action":              no_action,
        "flush_io_queue":         flush_io_queue,
        "checkpoint_and_restart": checkpoint_and_restart,
        "escalate":               escalate,
    }
    fn = dispatch.get(action, no_action)
    result = fn(container)
    result["mode"]         = "real"
    result["wasm_blocked"] = False
    result["wasm_reason"]  = wasm_reason   # "WASM sandbox: policy satisfied"
    return result


# --- Self-test ---
if __name__ == "__main__":
    # Decision dicts must include fsm_state_after so the WASM sandbox
    # receives the correct post-transition FSM state (as reconcile.py provides).

    # no_action — always allowed (any FSM state)
    d = execute({"container": "postgres", "action": "no_action",
                 "mode": "real", "fsm_state_after": "Healthy", "score": 0.0})
    assert d["success"] is True and d["action"] == "no_action", d
    assert d["wasm_blocked"] is False, d

    # flush_io_queue — allowed from Degraded with score >= 0.50
    d = execute({"container": "postgres", "action": "flush_io_queue",
                 "mode": "real", "fsm_state_after": "Degraded", "score": 0.65})
    assert d["success"] is True and d["stdout"] != "", d
    assert d["wasm_blocked"] is False, d

    # flush_io_queue — WASM blocks it from Healthy (no degradation present)
    d = execute({"container": "postgres", "action": "flush_io_queue",
                 "mode": "real", "fsm_state_after": "Healthy", "score": 0.65})
    assert d["wasm_blocked"] is True, d
    print(f"  flush/Healthy: WASM-blocked as expected ✓")

    # escalate — allowed from Degraded with score >= 0.95
    d = execute({"container": "mysql", "action": "escalate",
                 "mode": "real", "fsm_state_after": "Degraded", "score": 0.97})
    assert d["success"] is True and d["stdout"] != "", d
    assert d["wasm_blocked"] is False, d

    # shadow — must not execute; WASM check is skipped in shadow mode
    d = execute({"container": "postgres", "action": "checkpoint_and_restart",
                 "mode": "shadow", "fsm_state_after": "Audited", "score": 0.85})
    assert d["mode"] == "shadow" and d["success"] is None, d
    assert "SHADOW" in d["stdout"], d

    # checkpoint_and_restart — allowed from Audited (docker may not be present / CRIU may fail)
    # Either CRIU path or fallback restart path is acceptable; wasm_blocked must be False.
    d = execute({"container": "nonexistent_uc_f_test", "action": "checkpoint_and_restart",
                 "mode": "real", "fsm_state_after": "Audited", "score": 0.85})
    assert d["action"] == "checkpoint_and_restart", d
    assert d["wasm_blocked"] is False, d
    assert "checkpoint_success" in d, f"checkpoint_success field missing: {d}"
    assert "checkpoint_latency_ms" in d, f"checkpoint_latency_ms field missing: {d}"
    assert "criu_used" in d, f"criu_used field missing: {d}"
    print(
        f"  checkpoint_and_restart: criu_used={d['criu_used']} "
        f"checkpoint_success={d['checkpoint_success']} "
        f"restore_success={d['restore_success']} "
        f"latency={d['checkpoint_latency_ms']}ms"
    )

    # checkpoint_and_restart — WASM blocks it from Degraded (must be Audited first)
    d = execute({"container": "postgres", "action": "checkpoint_and_restart",
                 "mode": "real", "fsm_state_after": "Degraded", "score": 0.85})
    assert d["wasm_blocked"] is True, d
    print(f"  checkpoint/Degraded: WASM-blocked as expected ✓")

    # Reversibility catalogue sanity
    assert REVERSIBILITY["flush_io_queue"]         == "reversible"
    assert REVERSIBILITY["escalate"]               == "conditional"
    assert REVERSIBILITY["volume_delete"]          == "irreversible"
    assert "volume_delete" in IRREVERSIBLE_ACTIONS

    # WASM latency stats — should have accumulated samples from above calls
    stats = wasm_latency_stats()
    assert stats["count"] > 0, "No latency samples collected"
    print(
        f"  WASM latency: n={stats['count']} "
        f"mean={stats['mean_us']}µs stddev={stats['stddev_us']}µs "
        f"engine={stats['engine']}"
    )

    print("\nAll self-tests passed.")
