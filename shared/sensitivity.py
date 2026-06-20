# shared/sensitivity.py — Threshold Sensitivity Analysis (trace-replay)
#
# Sweeps FLUSH_THRESHOLD and REPAIR_THRESHOLD over existing stateful/traces.jsonl.
# Outputs shared/sensitivity_results.json (JSON).
#
# NOTE: evaluation/sensitivity.py is the trial-based sweep used for paper results.
# This file is a lighter replay-only variant for quick local exploration.
#
# Data source:  stateful/traces.jsonl  (real-mode entries only, read-only)
# Results out:  shared/sensitivity_results.json
#
# Method:
#   Each real-mode stateful trace entry stores a 'score' (0.0–0.97) computed by
#   stateful/confidence.py for that cycle.  Re-applying different threshold values
#   to these scores gives the counterfactual action distribution without FSM
#   gating — the input signal is held constant while only the thresholds vary.
#
#   FSM gating is excluded from this sweep because FSM state evolves over time
#   and cannot be faithfully reconstructed from individual trace entries.  The
#   sweep therefore measures threshold sensitivity of the raw scoring layer.
#
# Score values in the stateful trace data
# ----------------------------------------
#   The confidence scorer produces a discrete set of values:
#     I/O latency  : n/5 * 0.85  for n in {1..5}  → {0.17, 0.34, 0.51, 0.68, 0.85}
#     VFS errors   : n/3 * 0.88  for n in {1..3}  → {0.293, 0.587, 0.88}
#     Volume loss  : 0.97 (fixed)
#   These values are stored in 'score' in traces.jsonl and are the inputs to the sweep.
#
# Sweep parameters
# ----------------
#   FLUSH_THRESHOLD  swept: [0.20, 0.30, 0.40, 0.50, 0.60, 0.70]   (current: 0.50)
#   REPAIR_THRESHOLD swept: [0.50, 0.60, 0.70, 0.80, 0.90]          (current: 0.80)
#   ESCALATE_THRESHOLD is fixed at 0.95 (volume_loss score is 0.97;
#     sweeping escalate threshold would binary-toggle the 1,570 escalate actions)
#
# Usage (from repo root):
#   python3 shared/sensitivity.py

import json
import os
import sys
import time
import collections
import itertools

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(_here)

STATEFUL_TRACES = os.path.join(_repo, "stateful", "traces.jsonl")
OUT_FILE        = os.path.join(_here, "sensitivity_results.json")

# Current production thresholds (stateful/reconcile.py)
BASELINE_FLUSH    = 0.50
BASELINE_REPAIR   = 0.80
ESCALATE_THRESHOLD = 0.95   # fixed for this sweep

# Sweep ranges
FLUSH_VALUES  = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70]
REPAIR_VALUES = [0.50, 0.60, 0.70, 0.80, 0.90]


def _load_scores(path: str) -> list:
    """Load (score, baseline_action) pairs from real stateful traces."""
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
                rows.append({
                    "score":           t["score"],
                    "baseline_action": t["action"],
                })
    return rows


def _decide(score: float, flush: float, repair: float, escalate: float) -> str:
    """Pure threshold DAG — no FSM gating (see module docstring)."""
    if score >= escalate:
        return "escalate"
    if score >= repair:
        return "checkpoint_and_restart"
    if score >= flush:
        return "flush_io_queue"
    return "no_action"


def _distribution(actions: list) -> dict:
    total = len(actions)
    if not total:
        return {}
    counts = collections.Counter(actions)
    return {a: round(counts[a] / total, 4) for a in sorted(counts)}


def _agreement(baseline: list, variant: list) -> float:
    if not baseline:
        return 0.0
    return round(sum(b == v for b, v in zip(baseline, variant)) / len(baseline), 4)


def run(trace_file: str = STATEFUL_TRACES, out_file: str = OUT_FILE) -> dict:
    if not os.path.exists(trace_file):
        raise FileNotFoundError(f"Trace file not found: {trace_file}")

    rows = _load_scores(trace_file)
    n = len(rows)
    print(f"[sensitivity] Loaded {n} real stateful trace entries")

    scores           = [r["score"]           for r in rows]
    baseline_actions = [r["baseline_action"] for r in rows]

    # Score distribution (how many entries at each discrete value)
    score_dist = dict(
        collections.Counter(round(s, 4) for s in scores)
    )

    sweep_rows = []
    for flush, repair in itertools.product(FLUSH_VALUES, REPAIR_VALUES):
        # Constraint: flush must be strictly below repair
        if flush >= repair:
            continue
        variant_actions = [_decide(s, flush, repair, ESCALATE_THRESHOLD) for s in scores]
        dist = _distribution(variant_actions)
        sweep_rows.append({
            "flush_threshold":    flush,
            "repair_threshold":   repair,
            "escalate_threshold": ESCALATE_THRESHOLD,
            "is_baseline":        (flush == BASELINE_FLUSH and repair == BASELINE_REPAIR),
            "agreement_with_baseline": _agreement(baseline_actions, variant_actions),
            "action_distribution": dist,
        })

    result = {
        "generated_at":        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_file":         trace_file,
        "n_traces":            n,
        "baseline_thresholds": {
            "FLUSH_THRESHOLD":    BASELINE_FLUSH,
            "REPAIR_THRESHOLD":   BASELINE_REPAIR,
            "ESCALATE_THRESHOLD": ESCALATE_THRESHOLD,
        },
        "note": (
            "FSM gating is excluded from this sweep.  Results show threshold "
            "sensitivity of the scoring layer only.  The sweep re-applies different "
            "threshold values to 'score' fields stored in trace entries — the input "
            "signal is held constant."
        ),
        "score_distribution": score_dist,
        "sweep": sweep_rows,
    }

    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[sensitivity] Results written to {out_file}")
    print(f"\n[sensitivity] Sweep table (flush_thresh, repair_thresh → action_distribution, agreement):")
    print(f"  {'flush':>6}  {'repair':>6}  {'baseline':>8}  {'no_act':>8}  "
          f"{'flush_q':>8}  {'repair_q':>8}  {'escalate':>8}  {'agree':>6}")
    for row in sweep_rows:
        dist  = row["action_distribution"]
        base  = "*" if row["is_baseline"] else " "
        print(
            f"  {row['flush_threshold']:>6.2f}  {row['repair_threshold']:>6.2f}"
            f"  {base:>8}"
            f"  {dist.get('no_action', 0.0):>8.4f}"
            f"  {dist.get('flush_io_queue', 0.0):>8.4f}"
            f"  {dist.get('checkpoint_and_restart', 0.0):>8.4f}"
            f"  {dist.get('escalate', 0.0):>8.4f}"
            f"  {row['agreement_with_baseline']:>6.4f}"
        )

    return result


if __name__ == "__main__":
    run()
