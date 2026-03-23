# evaluate.py — computes evaluation metrics from replay output
#
# Reads:
#   traces.jsonl     — UnderCurrent decisions (produced by replay.py)
#   ground_truth.json — known fault injection times (produced by replay.py)
#
# Metrics computed:
#   - Detection Rate      (did the system act on faults?)
#   - False Positive Rate (did it act on non-faults?)
#   - Response Latency    (how fast did it respond?)
#   - Shadow vs Real Agreement Rate
#   - Per-fault-type breakdown

import json
import os
import sys
from collections import defaultdict

TRACES_FILE      = os.path.join(os.path.dirname(__file__), "..", "traces.jsonl")
GROUND_TRUTH_FILE = os.path.join(os.path.dirname(__file__), "ground_truth.json")
DETECTION_WINDOW  = 60   # seconds after fault injection to look for a decision


def load_traces():
    if not os.path.exists(TRACES_FILE):
        print(f"ERROR: {TRACES_FILE} not found. Run replay.py first.")
        sys.exit(1)
    with open(TRACES_FILE) as f:
        return [json.loads(l) for l in f if l.strip()]


def load_ground_truth():
    if not os.path.exists(GROUND_TRUTH_FILE):
        print(f"ERROR: {GROUND_TRUTH_FILE} not found. Run replay.py first.")
        sys.exit(1)
    with open(GROUND_TRUTH_FILE) as f:
        return json.load(f)


def detection_rate(traces: list, ground_truth: list) -> dict:
    """
    For each known fault, check if UnderCurrent took a non-trivial action
    (restart or reschedule) within DETECTION_WINDOW seconds of injection.
    """
    real_actions = [t for t in traces
                    if t["mode"] == "real" and t["action"] != "no_action"]

    detected   = []
    undetected = []

    for fault in ground_truth:
        container   = fault["container"]
        inject_time = fault["inject_time"]
        window_end  = inject_time + DETECTION_WINDOW

        # Look for any real action on this container within the window
        match = next(
            (t for t in real_actions
             if t["container"] == container
             and inject_time <= t.get("decision_timestamp", t.get("trace_time", 0)) <= window_end),
            None
        )

        if match:
            latency = match.get("decision_timestamp",
                                match.get("trace_time", inject_time)) - inject_time
            detected.append({
                "fault":   fault,
                "action":  match["action"],
                "latency": round(latency, 3),
            })
        else:
            undetected.append(fault)

    rate = len(detected) / len(ground_truth) * 100 if ground_truth else 0.0
    return {
        "detected":    len(detected),
        "undetected":  len(undetected),
        "total_faults": len(ground_truth),
        "rate":        round(rate, 2),
        "details":     detected,
        "missed":      undetected,
    }


def false_positive_rate(traces: list, ground_truth: list) -> dict:
    """
    Actions taken by the real path that do NOT correspond to any known fault
    injection within DETECTION_WINDOW seconds before the action.
    """
    real_actions = [t for t in traces
                    if t["mode"] == "real" and t["action"] != "no_action"]

    false_positives = []
    true_positives  = []

    for action in real_actions:
        container   = action["container"]
        action_time = action.get("decision_timestamp", action.get("trace_time", 0))

        # Is there a fault that could explain this action?
        match = next(
            (f for f in ground_truth
             if f["container"] == container
             and action_time - DETECTION_WINDOW <= f["inject_time"] <= action_time + DETECTION_WINDOW),
            None
        )

        if match:
            true_positives.append(action)
        else:
            false_positives.append(action)

    total = len(real_actions)
    rate  = len(false_positives) / total * 100 if total else 0.0
    return {
        "false_positives": len(false_positives),
        "true_positives":  len(true_positives),
        "total_actions":   total,
        "rate":            round(rate, 2),
        "examples":        false_positives[:5],
    }


def response_latency(detection_result: dict) -> dict:
    """Compute latency statistics from detection results."""
    latencies = [d["latency"] for d in detection_result["details"] if d["latency"] >= 0]
    if not latencies:
        return {"min": None, "max": None, "avg": None, "p95": None}

    latencies.sort()
    n = len(latencies)
    p95_idx = int(0.95 * n)

    return {
        "min":    round(min(latencies), 3),
        "max":    round(max(latencies), 3),
        "avg":    round(sum(latencies) / n, 3),
        "p95":    round(latencies[p95_idx], 3),
        "samples": n,
    }


def shadow_agreement(traces: list) -> dict:
    """
    Compare real vs shadow decisions for the same container at the same time.
    Groups by container + approximate timestamp bucket (5s window).
    """
    def bucket(t):
        ts = t.get("decision_timestamp", t.get("trace_time", 0))
        return (t["container"], int(ts // 5))

    real_map   = {bucket(t): t["action"] for t in traces if t["mode"] == "real"}
    shadow_map = {bucket(t): t["action"] for t in traces if t["mode"] == "shadow"}

    common = set(real_map.keys()) & set(shadow_map.keys())
    if not common:
        return {"agreement_rate": None, "agreements": 0, "disagreements": 0, "total": 0}

    agreements    = sum(1 for k in common if real_map[k] == shadow_map[k])
    disagreements = len(common) - agreements

    return {
        "agreement_rate": round(agreements / len(common) * 100, 2),
        "agreements":     agreements,
        "disagreements":  disagreements,
        "total":          len(common),
    }


def per_fault_type_breakdown(detection_result: dict) -> dict:
    """Break down detection rate by fault type."""
    by_type = defaultdict(lambda: {"detected": 0, "total": 0})

    for fault in detection_result["missed"]:
        ft = fault.get("fault_type", "unknown")
        by_type[ft]["total"] += 1

    for item in detection_result["details"]:
        ft = item["fault"].get("fault_type", "unknown")
        by_type[ft]["detected"] += 1
        by_type[ft]["total"] += 1

    result = {}
    for ft, counts in by_type.items():
        rate = counts["detected"] / counts["total"] * 100 if counts["total"] else 0
        result[ft] = {
            "detected": counts["detected"],
            "total":    counts["total"],
            "rate":     round(rate, 2),
        }
    return result


def print_report(dr, fpr, latency, shadow, breakdown):
    SEP = "─" * 52

    print(f"\n{'═'*52}")
    print(f"  UnderCurrent — Evaluation Report")
    print(f"{'═'*52}\n")

    print(f"  DETECTION RATE")
    print(SEP)
    print(f"  Faults injected:     {dr['total_faults']}")
    print(f"  Detected:            {dr['detected']}")
    print(f"  Missed:              {dr['undetected']}")
    print(f"  Detection rate:      {dr['rate']}%")
    print()

    print(f"  RESPONSE LATENCY (seconds)")
    print(SEP)
    if latency["avg"] is not None:
        print(f"  Min:   {latency['min']}s")
        print(f"  Avg:   {latency['avg']}s")
        print(f"  P95:   {latency['p95']}s")
        print(f"  Max:   {latency['max']}s")
    else:
        print(f"  No latency data (no detections)")
    print()

    print(f"  FALSE POSITIVE RATE")
    print(SEP)
    print(f"  Total real actions:  {fpr['total_actions']}")
    print(f"  True positives:      {fpr['true_positives']}")
    print(f"  False positives:     {fpr['false_positives']}")
    print(f"  False positive rate: {fpr['rate']}%")
    print()

    print(f"  SHADOW vs REAL AGREEMENT")
    print(SEP)
    if shadow["agreement_rate"] is not None:
        print(f"  Compared pairs:      {shadow['total']}")
        print(f"  Agreements:          {shadow['agreements']}")
        print(f"  Disagreements:       {shadow['disagreements']}")
        print(f"  Agreement rate:      {shadow['agreement_rate']}%")
    else:
        print(f"  No shadow traces found (run without --shadow to get both)")
    print()

    print(f"  PER-FAULT-TYPE BREAKDOWN")
    print(SEP)
    for fault_type, stats in sorted(breakdown.items()):
        bar_len = int(stats["rate"] / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {fault_type:<20} {bar}  {stats['rate']:5.1f}%  ({stats['detected']}/{stats['total']})")
    print()
    print(f"{'═'*52}\n")


def main():
    traces       = load_traces()
    ground_truth = load_ground_truth()

    print(f"[evaluate] {len(traces)} trace entries, {len(ground_truth)} ground truth faults")

    dr        = detection_rate(traces, ground_truth)
    fpr       = false_positive_rate(traces, ground_truth)
    latency   = response_latency(dr)
    shadow    = shadow_agreement(traces)
    breakdown = per_fault_type_breakdown(dr)

    print_report(dr, fpr, latency, shadow, breakdown)

    # Save JSON report
    report = {
        "detection":      dr,
        "false_positives": fpr,
        "latency":         latency,
        "shadow_agreement": shadow,
        "per_fault_type":  breakdown,
    }
    report_file = os.path.join(os.path.dirname(__file__), "evaluation_report.json")
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[evaluate] full report saved → {report_file}")


if __name__ == "__main__":
    main()
