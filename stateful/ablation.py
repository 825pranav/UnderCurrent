# stateful/ablation.py — Ablation Study for the Stateful Controller
#
# Re-processes existing trace scores to produce counterfactual decisions under
# two ablation variants.  NO new events are generated; all inputs are read from
# stateful/traces.jsonl (real-mode entries only).
#
# Data source:  stateful/traces.jsonl  (read-only)
# Results out:  stateful/ablation_results.json
#
# Why score-replay is valid:
#   Each trace entry records the 'score' already computed by confidence.py for
#   that cycle.  Re-applying different decision logic to the same score gives a
#   faithful counterfactual — the input signal is held constant while the
#   decision mechanism varies.
#
# Window-dependent variants (short_window / long_window) are NOT implemented
# here because raw event timestamps within each window are not stored in the
# trace.  Those variants would require re-running the main loop with a different
# WINDOW_SECONDS constant.
#
# Ablation variants
# -----------------
# baseline      — actual decisions recorded in traces (ground truth)
# no_fsm        — score-only decision; FSM gating removed.
#                 Threshold logic is identical to baseline but steps 2-7 of
#                 stateful/reconcile.py are skipped.  checkpoint_and_restart
#                 is dispatched whenever score >= REPAIR_THRESHOLD regardless
#                 of FSM state.
# no_confidence — binary scoring: any fault kernel signal present → score 0.75
#                 (above FLUSH and REPAIR, below ESCALATE); no signal → 0.0.
#                 Tests whether the graduated continuous score matters.
#
# Comparison metrics (per variant vs baseline):
#   agreement_rate     — fraction of real decisions that match the baseline action
#   action_distribution — fraction of each action type in variant decisions
#
# Usage (from stateful/ directory):
#   python3 ablation.py
# Usage (from repo root):
#   python3 stateful/ablation.py

import json
import os
import sys
import collections
import time

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(_here)

TRACE_FILE  = os.path.join(_here, "traces.jsonl")
OUT_FILE    = os.path.join(_here, "ablation_results.json")

# Thresholds — must mirror stateful/reconcile.py exactly for 'baseline' to be reproducible
FLUSH_THRESHOLD    = 0.50
REPAIR_THRESHOLD   = 0.80
ESCALATE_THRESHOLD = 0.95

# Fault signals that indicate a non-nominal state (used by no_confidence variant)
_FAULT_SIGNALS = frozenset({
    "blk_io_latency_high", "vfs_write_error", "vfs_read_error",
    "volume_mount_lost",
})


def _load_real_stateful(path: str) -> list:
    """Load real-mode stateful trace entries from path."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if t.get("mode") == "real" and t.get("node_type") == "F":
                rows.append(t)
    return rows


def _decide_no_fsm(score: float) -> str:
    """
    Pure threshold DAG — same edges as reconcile.py but no FSM gating.
    checkpoint_and_restart is dispatched whenever score >= REPAIR_THRESHOLD,
    regardless of FSM state.
    """
    if score >= ESCALATE_THRESHOLD:
        return "escalate"
    if score >= REPAIR_THRESHOLD:
        return "checkpoint_and_restart"
    if score >= FLUSH_THRESHOLD:
        return "flush_io_queue"
    return "no_action"


def _decide_threshold_only(score: float) -> str:
    """
    Threshold-only baseline (equivalent to the shadow path — fresh FSM each cycle).
    A fresh FSM always starts at Healthy: degrade fires on fault, making FSM Degraded.
    C&R gate requires Audited, but Degraded != Audited, so C&R is always downgraded
    to flush_io_queue within a single cycle.  This baseline can never approve C&R.
    """
    if score >= ESCALATE_THRESHOLD:
        return "escalate"
    if score >= REPAIR_THRESHOLD:
        return "flush_io_queue"   # would be C&R but fresh FSM blocks it every time
    if score >= FLUSH_THRESHOLD:
        return "flush_io_queue"
    return "no_action"


def _decide_no_confidence(kernel_signals: list) -> str:
    """
    Binary scoring variant.
    If any fault kernel signal is present, assign score = 0.75 (above FLUSH
    and REPAIR thresholds, below ESCALATE).  Otherwise score = 0.0.
    score = 0.75 → checkpoint_and_restart under threshold logic.
    """
    signals = set(kernel_signals or [])
    binary_score = 0.75 if signals & _FAULT_SIGNALS else 0.0
    return _decide_no_fsm(binary_score)


def _action_distribution(actions: list) -> dict:
    total = len(actions)
    if total == 0:
        return {}
    counts = collections.Counter(actions)
    return {
        action: {
            "count": cnt,
            "fraction": round(cnt / total, 4),
        }
        for action, cnt in sorted(counts.items())
    }


def _agreement_rate(baseline_actions: list, variant_actions: list) -> float:
    assert len(baseline_actions) == len(variant_actions)
    if not baseline_actions:
        return 0.0
    matches = sum(b == v for b, v in zip(baseline_actions, variant_actions))
    return round(matches / len(baseline_actions), 4)


def run(trace_file: str = TRACE_FILE, out_file: str = OUT_FILE) -> dict:
    """
    Load real stateful traces, compute ablation variant decisions,
    and write results to out_file.
    """
    if not os.path.exists(trace_file):
        raise FileNotFoundError(f"Trace file not found: {trace_file}")

    traces = _load_real_stateful(trace_file)
    n = len(traces)
    print(f"[ablation] Loaded {n} real stateful trace entries from {trace_file}")

    baseline_actions   = [t["action"]       for t in traces]
    scores             = [t["score"]        for t in traces]
    kernel_signals_col = [t.get("kernel_signals", []) for t in traces]

    no_fsm_actions         = [_decide_no_fsm(s)             for s in scores]
    threshold_only_actions = [_decide_threshold_only(s)     for s in scores]
    no_conf_actions        = [_decide_no_confidence(ks)     for ks in kernel_signals_col]

    results = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_file":  trace_file,
        "n_traces":     n,
        "thresholds_used": {
            "FLUSH_THRESHOLD":    FLUSH_THRESHOLD,
            "REPAIR_THRESHOLD":   REPAIR_THRESHOLD,
            "ESCALATE_THRESHOLD": ESCALATE_THRESHOLD,
        },
        "note": (
            "Ablation variants re-apply decision logic to 'score' values already "
            "stored in trace entries.  The input signal is held constant; only the "
            "decision mechanism varies.  Window-dependent variants (short_window, "
            "long_window) are not implemented because raw per-window event counts "
            "are not stored in trace entries."
        ),
        "variants": {
            "baseline": {
                "description": "Actual decisions recorded in stateful/traces.jsonl (ground truth).",
                "action_distribution": _action_distribution(baseline_actions),
                "agreement_with_baseline": 1.0,
            },
            "no_fsm": {
                "description": (
                    "FSM gating removed — always-restart baseline.  "
                    "checkpoint_and_restart dispatched whenever score >= 0.80 "
                    "regardless of FSM state (no audit phase required).  "
                    "Source: _decide_no_fsm() in this file."
                ),
                "action_distribution": _action_distribution(no_fsm_actions),
                "agreement_with_baseline": _agreement_rate(baseline_actions, no_fsm_actions),
                "disagreements": {
                    "total": sum(b != v for b, v in zip(baseline_actions, no_fsm_actions)),
                    "baseline_flush_nofs_repair_UNSAFE": sum(
                        b == "flush_io_queue" and v == "checkpoint_and_restart"
                        for b, v in zip(baseline_actions, no_fsm_actions)
                    ),
                    "baseline_repair_nofs_flush": sum(
                        b == "checkpoint_and_restart" and v == "flush_io_queue"
                        for b, v in zip(baseline_actions, no_fsm_actions)
                    ),
                },
                "premature_cr_fires": sum(
                    v == "checkpoint_and_restart" and b == "flush_io_queue"
                    for b, v in zip(baseline_actions, no_fsm_actions)
                ),
            },
            "threshold_only": {
                "description": (
                    "Shadow-path baseline — fresh FSM every cycle.  "
                    "Equivalent to a memoryless threshold controller: C&R is always "
                    "downgraded to flush_io_queue because a fresh FSM starts at Healthy "
                    "and cannot reach Audited within a single cycle.  "
                    "Source: _decide_threshold_only() in this file."
                ),
                "action_distribution": _action_distribution(threshold_only_actions),
                "agreement_with_baseline": _agreement_rate(baseline_actions, threshold_only_actions),
                "disagreements": {
                    "total": sum(b != v for b, v in zip(baseline_actions, threshold_only_actions)),
                    "baseline_repair_to_flush": sum(
                        b == "checkpoint_and_restart" and v == "flush_io_queue"
                        for b, v in zip(baseline_actions, threshold_only_actions)
                    ),
                },
                "missed_cr_fires": sum(
                    b == "checkpoint_and_restart" and v == "flush_io_queue"
                    for b, v in zip(baseline_actions, threshold_only_actions)
                ),
            },
            "no_confidence": {
                "description": (
                    "Continuous confidence score replaced by binary signal presence. "
                    "Any fault kernel signal → score=0.75 (→ checkpoint_and_restart). "
                    "No fault signals → score=0.0 (→ no_action). "
                    "Fault signals: blk_io_latency_high, vfs_write_error, vfs_read_error, "
                    "volume_mount_lost."
                ),
                "action_distribution": _action_distribution(no_conf_actions),
                "agreement_with_baseline": _agreement_rate(baseline_actions, no_conf_actions),
                "disagreements": {
                    "total": sum(b != v for b, v in zip(baseline_actions, no_conf_actions)),
                },
            },
        },
    }

    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"[ablation] Results written to {out_file}")
    print(f"\n[ablation] Agreement rates vs baseline:")
    for vname, vdata in results["variants"].items():
        print(f"  {vname:<20} agreement={vdata['agreement_with_baseline']}")
    print(f"\n[ablation] Action distributions:")
    for vname, vdata in results["variants"].items():
        dist = {k: v["fraction"] for k, v in vdata["action_distribution"].items()}
        print(f"  {vname:<20} {dist}")

    return results


if __name__ == "__main__":
    run()
