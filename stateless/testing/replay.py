# replay.py — replays a dataset through the UnderCurrent pipeline
# and collects decisions for evaluation.
#
# Usage:
#   python3 replay.py --dataset synthetic
#   python3 replay.py --dataset rcaeval  --data-dir /path/to/rcaeval
#   python3 replay.py --dataset alibaba  --data-file /path/to/trace.csv

import argparse
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from state_store import StateStore
from confidence import score_all
from reconcile import _decide
from actions import execute
from trace_logger import log_decision
from dataset_loader import load_synthetic, load_rcaeval, load_alibaba

WINDOW_SECONDS = 60


def replay(events: list, ground_truth: list, shadow: bool = False) -> list:
    """
    Feed events into UnderCurrent pipeline in time order.
    After each event, check if a reconcile is needed based on the
    sliding window and run the decision engine.

    Returns list of all decisions made.
    """
    store     = StateStore(window_seconds=WINDOW_SECONDS)
    decisions = []
    last_reconcile_time = None

    # Shift all event timestamps to be relative to now so StateStore's
    # sliding window (which uses time.time()) doesn't prune them immediately
    if events:
        oldest = events[0]["time"]
        now    = time.time()
        offset = now - oldest
        for e in events:
            e["time"] += offset
        for g in ground_truth:
            g["inject_time"] += offset

    print(f"\n[replay] replaying {len(events)} events over "
          f"{len(set(e['container'] for e in events))} containers\n")

    for event in events:
        store.record(event)

        # Reconcile every time a new "window" of events arrives
        # (simulate the reconcile loop firing after each batch)
        current_time = event["time"]
        if last_reconcile_time is None or (current_time - last_reconcile_time) >= 5:
            last_reconcile_time = current_time
            scores = score_all(store)

            for container, score in scores.items():
                decision = _decide(container, score)
                decision["mode"] = "shadow" if shadow else "real"
                decision["timestamp"] = current_time

                result = execute(decision)
                log_decision(decision, result)
                decisions.append(decision)

    print(f"[replay] done — {len(decisions)} decisions made")
    return decisions


def main():
    parser = argparse.ArgumentParser(description="UnderCurrent Dataset Replay")
    parser.add_argument("--dataset",   choices=["synthetic", "rcaeval", "alibaba"],
                        default="synthetic")
    parser.add_argument("--data-dir",  help="Path to RCAEval dataset directory")
    parser.add_argument("--data-file", help="Path to Alibaba CSV file")
    parser.add_argument("--shadow",    action="store_true")
    parser.add_argument("--faults",    type=int, default=20,
                        help="Number of faults for synthetic dataset")
    args = parser.parse_args()

    if args.dataset == "synthetic":
        events, ground_truth = load_synthetic(n_faults=args.faults)
    elif args.dataset == "rcaeval":
        if not args.data_dir:
            print("ERROR: --data-dir required for rcaeval dataset")
            sys.exit(1)
        events, ground_truth = load_rcaeval(args.data_dir)
    elif args.dataset == "alibaba":
        if not args.data_file:
            print("ERROR: --data-file required for alibaba dataset")
            sys.exit(1)
        events, ground_truth = load_alibaba(args.data_file)

    decisions = replay(events, ground_truth, shadow=args.shadow)

    # Save ground truth alongside traces for evaluation
    import json
    gt_file = os.path.join(os.path.dirname(__file__), "ground_truth.json")
    with open(gt_file, "w") as f:
        json.dump(ground_truth, f, indent=2)
    print(f"[replay] ground truth saved → {gt_file}")
    print(f"[replay] decisions saved    → traces.jsonl")
    print(f"\nRun: python3 testing/evaluate.py")


if __name__ == "__main__":
    main()
