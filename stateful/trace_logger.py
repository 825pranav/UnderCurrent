# trace_logger.py — F7: Stateful Decision Trace Logger
#
# Writes decision traces to stateful/traces.jsonl (append-only JSON lines).
#
# Schema version: 1.2.0 (see shared/trace_schema.py for the full field catalogue).
# All base fields are present in every entry; stateful extension fields are additive.
# A consumer reading this file alongside stateless/traces.jsonl will see unknown
# keys in stateful entries and must ignore them gracefully (standard JSON tolerance).
#
#   Base fields (both tracks):
#     trace_time, container, score, action, mode, why,
#     executed, stdout, stderr, decision_timestamp, action_timestamp
#
#   Stateful extension fields:
#     node_type, fsm_state, fsm_state_after, fsm_transition, reversibility,
#     kernel_signals, dag_pattern, blocked_reason, wasm_blocked, wasm_reason
#
# Trace file: stateful/traces.jsonl  (separate from stateless/traces.jsonl)

import json
import os
import sys
import time

TRACE_FILE = os.path.join(os.path.dirname(__file__), "traces.jsonl")

# Make shared/ importable for SCHEMA_VERSION
_shared = os.path.join(os.path.dirname(__file__), "..", "shared")
if _shared not in sys.path:
    sys.path.insert(0, _shared)

try:
    from trace_schema import SCHEMA_VERSION
except ImportError:
    SCHEMA_VERSION = "1.3.0"


def log_decision(decision: dict, action_result: dict) -> dict:
    """
    Build and append one trace entry to stateful/traces.jsonl.

    decision      — dict from stateful/reconcile.py
    action_result — dict from stateful/actions.py

    Returns the trace entry dict written to disk.
    """
    entry = {
        # ── Base fields (present in both S and F traces — see trace_schema.py) ─
        "trace_time":         time.time(),
        "container":          decision.get("container"),
        "score":              decision.get("score"),
        "action":             decision.get("action"),
        "mode":               decision.get("mode", "real"),
        "why":                decision.get("reason", ""),
        "executed":           action_result.get("success"),   # None in shadow mode
        "stdout":             action_result.get("stdout", ""),
        "stderr":             action_result.get("stderr", ""),
        "decision_timestamp": decision.get("timestamp"),
        "action_timestamp":   action_result.get("timestamp"),
        # ── Stateful extension fields ─────────────────────────────────────────
        "node_type":          "F",
        "fsm_state":          decision.get("fsm_state"),
        "fsm_state_after":    decision.get("fsm_state_after"),
        "fsm_transition":     decision.get("fsm_transition"),   # e.g. "Healthy→Degraded"
        "reversibility":      (
            action_result.get("reversibility")
            or decision.get("reversibility")
        ),
        "kernel_signals":     decision.get("kernel_signals", []),
        "dag_pattern":        decision.get("dag_pattern"),
        "blocked_reason":     decision.get("blocked_reason"),
        # ── WASM sandbox fields — Unsafe-action Suppression Rate metric ──────
        "wasm_blocked":       action_result.get("wasm_blocked", False),
        "wasm_reason":        action_result.get("wasm_reason"),
    }

    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    tag = "[SHADOW]" if entry["mode"] == "shadow" else "[REAL]"
    print(
        f"[trace-f]{tag} {entry['container']} | score={entry['score']} "
        f"| fsm={entry['fsm_state']} | action={entry['action']} "
        f"| why: {entry['why']}",
        flush=True,
    )
    return entry


def read_traces(n: int = None) -> list:
    """
    Read traces from stateful/traces.jsonl.
    n=None → all entries; n=k → last k entries.
    """
    if not os.path.exists(TRACE_FILE):
        return []
    with open(TRACE_FILE) as f:
        lines = [json.loads(ln) for ln in f if ln.strip()]
    return lines[-n:] if n else lines


# --- Self-test ---
if __name__ == "__main__":
    import tempfile

    _orig = TRACE_FILE
    tmp   = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    TRACE_FILE = tmp.name   # redirect for test

    now = time.time()

    decisions = [
        {
            "container": "postgres", "score": 0.97, "action": "escalate",
            "reason": "score 0.97 >= escalate threshold 0.95",
            "mode": "real", "timestamp": now,
            "fsm_state": "Degraded", "fsm_transition": "Healthy→Degraded",
            "kernel_signals": ["blk_io_latency_high", "volume_mount_lost"],
            "dag_pattern": "io_degradation_critical", "blocked_reason": None,
        },
        {
            "container": "mongo", "score": 0.60, "action": "flush_io_queue",
            "reason": "score 0.60 >= flush threshold 0.50",
            "mode": "shadow", "timestamp": now,
            "fsm_state": "Healthy", "fsm_transition": "Healthy→Degraded",
            "kernel_signals": ["vfs_write_error"],
            "dag_pattern": "io_degradation_minor", "blocked_reason": None,
        },
        {
            "container": "etcd", "score": 0.30, "action": "no_action",
            "reason": "score 0.30 below flush threshold 0.50",
            "mode": "real", "timestamp": now,
            "fsm_state": "Healthy", "fsm_transition": None,
            "kernel_signals": ["blk_io_latency_normal"],
            "dag_pattern": "nominal", "blocked_reason": None,
        },
    ]
    results = [
        {"action": "escalate",       "success": True,  "stdout": "Escalation logged for postgres — on-call alert dispatched", "stderr": "", "timestamp": now, "reversibility": "conditional"},
        {"action": "flush_io_queue", "success": None,  "stdout": "SHADOW: would flush",     "stderr": "", "timestamp": now, "reversibility": "reversible"},
        {"action": "no_action",      "success": True,  "stdout": "",                        "stderr": "", "timestamp": now, "reversibility": "reversible"},
    ]

    entries = [log_decision(d, r) for d, r in zip(decisions, results)]
    assert len(entries) == 3
    assert entries[0]["node_type"] == "F"
    assert entries[0]["fsm_state"] == "Degraded"
    assert entries[1]["mode"] == "shadow"
    assert entries[2]["action"] == "no_action"

    # Backward-compatibility check: every base field must be present in every entry
    BASE_FIELDS = [
        "trace_time", "container", "score", "action", "mode",
        "why", "executed", "stdout", "stderr",
        "decision_timestamp", "action_timestamp",
    ]
    for entry in entries:
        for field in BASE_FIELDS:
            assert field in entry, f"Missing base field '{field}' in entry: {entry}"

    traces = read_traces()
    assert len(traces) == 3, f"Expected 3 traces, got {len(traces)}"
    last1 = read_traces(n=1)
    assert last1[0]["container"] == "etcd"

    os.unlink(tmp.name)
    TRACE_FILE = _orig
    print("\nAll self-tests passed.")
