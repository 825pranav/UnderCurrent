# trace_logger.py — S7: Decision Trace Logger
# Logs every decision with full context to stateless/traces.jsonl.
# JSON-line format, append-only. Each entry includes explainability fields.

import json
import os
import time

TRACE_FILE = os.path.join(os.path.dirname(__file__), "traces.jsonl")


def log_decision(decision: dict, action_result: dict) -> dict:
    """
    Build and append a trace entry to traces.jsonl.

    decision     — from reconcile.py (container, score, action, reason, mode, timestamp)
    action_result — from actions.py  (success, stdout, stderr, timestamp, mode)

    Returns the trace entry dict.
    """
    entry = {
        "trace_time": time.time(),
        "container": decision.get("container"),
        "score": decision.get("score"),
        "action": decision.get("action"),
        "mode": decision.get("mode", "real"),          # "real" | "shadow"
        # Explainability: why was this action chosen?
        "why": decision.get("reason", ""),
        # Execution result
        "executed": action_result.get("success"),      # None if shadow
        "stdout": action_result.get("stdout", ""),
        "stderr": action_result.get("stderr", ""),
        "decision_timestamp": decision.get("timestamp"),
        "action_timestamp":   action_result.get("timestamp"),
        # ── Common extension fields (schema 1.2.0 — present in both S and F traces) ──
        "node_type":      "S",
        "kernel_signals": decision.get("kernel_signals", []),
        "dag_pattern":    decision.get("dag_pattern"),
        "reversibility":  action_result.get("reversibility"),
    }

    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    tag = "[SHADOW]" if entry["mode"] == "shadow" else "[REAL]"
    print(
        f"[trace]{tag} {entry['container']} | score={entry['score']} "
        f"| action={entry['action']} | why: {entry['why']}",
        flush=True,
    )
    return entry


def log_batch(decisions: list, action_results: list) -> list:
    """
    Log a parallel list of decisions and action_results.
    Returns list of trace entries.
    """
    assert len(decisions) == len(action_results), \
        "decisions and action_results must have the same length"
    return [log_decision(d, r) for d, r in zip(decisions, action_results)]


def read_traces(n: int = None) -> list:
    """
    Read traces from traces.jsonl.
    n=None → all entries; n=10 → last 10 entries.
    """
    if not os.path.exists(TRACE_FILE):
        return []
    with open(TRACE_FILE) as f:
        lines = [json.loads(ln) for ln in f if ln.strip()]
    return lines[-n:] if n else lines


# --- Self-test ---
if __name__ == "__main__":
    import tempfile

    # Redirect trace file to a temp file for testing
    _orig_trace_file = TRACE_FILE
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    TRACE_FILE = tmp.name  # override module-level global for this run

    now = time.time()

    decisions = [
        {"container": "nginx",  "score": 0.95, "action": "reschedule",
         "reason": "score 0.95 >= reschedule threshold 0.95",
         "mode": "real",   "timestamp": now},
        {"container": "redis",  "score": 0.90, "action": "restart",
         "reason": "score 0.9 >= restart threshold 0.8",
         "mode": "shadow", "timestamp": now},
        {"container": "pyapp",  "score": 0.54, "action": "no_action",
         "reason": "score 0.54 below restart threshold 0.8",
         "mode": "real",   "timestamp": now},
    ]

    action_results = [
        {"action": "reschedule", "success": True,  "stdout": "Scheduler: reschedule intent logged for nginx — no live orchestrator", "stderr": "", "timestamp": now},
        {"action": "restart",    "success": None,  "stdout": "SHADOW: would run restart",         "stderr": "", "timestamp": now, "mode": "shadow"},
        {"action": "no_action",  "success": True,  "stdout": "",                                  "stderr": "", "timestamp": now},
    ]

    entries = log_batch(decisions, action_results)
    assert len(entries) == 3
    assert entries[0]["why"] != ""
    assert entries[1]["mode"] == "shadow"
    assert entries[2]["action"] == "no_action"

    # Verify file was written
    traces = read_traces()
    assert len(traces) == 3, f"Expected 3 traces, got {len(traces)}"

    # Verify last N
    last1 = read_traces(n=1)
    assert last1[0]["container"] == "pyapp"

    os.unlink(tmp.name)
    TRACE_FILE = _orig_trace_file
    print("\nAll self-tests passed.")
