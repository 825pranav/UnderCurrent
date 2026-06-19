#!/usr/bin/env python3
"""
run_episodes.py — Drive N fault injection episodes against the stateful FSM.

MTTR definition used here:
  fault_time     = when blk_io fault events are injected
  first_cr_time  = when checkpoint_and_restart first fires (regardless of
                   whether CRIU restore succeeds — docker restart is the
                   fallback, so the container recovers either way)
  MTTR           = first_cr_time - fault_time

This matches the paper's structural claim: the FSM guarantees at least two
reconcile intervals between first fault detection and C&R execution
(flush fires in cycle 1; C&R fires in cycle 2 at earliest).

Each episode:
  1. Fresh StatefulStateStore + reset FSM to Healthy for the container.
  2. Inject 8 high-latency blk_io events directly into the store.
  3. Reconcile every `interval` seconds until checkpoint_and_restart fires
     or `timeout` seconds elapse.
  4. Record MTTR and write to episodes.jsonl.
  5. Cooldown `cooldown` seconds between episodes.

Outputs:
  scripts/episodes.jsonl    — one JSON object per episode
  scripts/mttr_summary.json — N, mean, stddev, min, max
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time

_REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_REPO, "stateful"))

from state_store import StatefulStateStore
from fsm import ContainerFSM
from reconcile import reconcile
from actions import execute

EPISODES_OUT = os.path.join(os.path.dirname(__file__), "episodes.jsonl")
SUMMARY_OUT  = os.path.join(os.path.dirname(__file__), "mttr_summary.json")


def _ensure_running(container: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or r.stdout.strip() != "true":
        print(f"[episodes] starting {container}")
        sr = subprocess.run(["docker", "start", container], capture_output=True)
        if sr.returncode != 0:
            print(f"[episodes] ERROR: cannot start {container}")
            return False
        time.sleep(2)
    return True


def _inject_fault(store: StatefulStateStore, container: str, n: int = 8):
    """Inject n high-latency blk_io events into the state store."""
    now = time.time()
    for i in range(n):
        store.record({
            "container":  container,
            "pid":        90000 + i,
            "event":      "blk_io_latency",
            "latency_us": 50_000 + i * 500,   # 50–54 ms, well above threshold
            "time":       now + i * 0.01,
            "node_type":  "F",
        })


def run_episode(n: int, container: str, interval: float, timeout: float) -> dict:
    """
    Single episode. Returns result dict.
    Creates a fresh store + FSM each time so episodes are independent.
    """
    store = StatefulStateStore(window_seconds=120)
    fsm   = ContainerFSM()

    fault_time     = time.time()
    first_cr_time  = None
    cycle          = 0
    states_visited = []
    actions_taken  = []

    _inject_fault(store, container)
    print(f"  [ep{n}] fault injected at t=0")

    deadline = fault_time + timeout
    while time.time() < deadline:
        time.sleep(interval)
        cycle += 1

        decisions = reconcile(store, fsm, shadow=False)
        for d in decisions:
            if d["container"] != container:
                continue

            action    = d["action"]
            fsm_state = d.get("fsm_state", "?")
            states_visited.append(fsm_state)
            actions_taken.append(action)

            result = execute(d)
            ckpt_ok = result.get("checkpoint_success", False)

            print(
                f"  [ep{n}] cycle={cycle} fsm={fsm_state} "
                f"action={action} success={result.get('success')} "
                + (f"ckpt={ckpt_ok}" if action == "checkpoint_and_restart" else "")
            )

            if action == "checkpoint_and_restart":
                first_cr_time = time.time()
                if result.get("success"):
                    fsm.apply(container, "recover")
                else:
                    fsm.apply(container, "degrade")
                # Episode complete — C&R fired
                break

        if first_cr_time is not None:
            break

    mttr = round(first_cr_time - fault_time, 2) if first_cr_time else None

    return {
        "episode":        n,
        "container":      container,
        "fault_time":     fault_time,
        "first_cr_time":  first_cr_time,
        "mttr_s":         mttr,
        "cycles_to_cr":   cycle,
        "states_visited": states_visited,
        "actions_taken":  actions_taken,
        "timed_out":      first_cr_time is None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default="postgres")
    ap.add_argument("--episodes",  type=int,   default=30)
    ap.add_argument("--interval",  type=float, default=5.0)
    ap.add_argument("--timeout",   type=float, default=120.0)
    ap.add_argument("--cooldown",  type=float, default=5.0)
    args = ap.parse_args()

    if not _ensure_running(args.container):
        sys.exit(1)

    mttrs   = []
    open(EPISODES_OUT, "w").close()

    for ep in range(1, args.episodes + 1):
        print(f"\n{'='*56}")
        print(f"[episodes] {ep}/{args.episodes}  container={args.container}")

        result = run_episode(ep, args.container, args.interval, args.timeout)

        with open(EPISODES_OUT, "a") as f:
            f.write(json.dumps(result) + "\n")

        if result["mttr_s"] is not None:
            mttrs.append(result["mttr_s"])
            print(f"[episodes] ep {ep}: MTTR={result['mttr_s']}s  "
                  f"cycles={result['cycles_to_cr']}  "
                  f"actions={result['actions_taken']}")
        else:
            print(f"[episodes] ep {ep}: TIMED OUT after {args.timeout}s")

        if ep < args.episodes:
            time.sleep(args.cooldown)

    n = len(mttrs)
    if n > 0:
        mean     = sum(mttrs) / n
        variance = sum((x - mean) ** 2 for x in mttrs) / n
        summary  = {
            "container":    args.container,
            "n_episodes":   args.episodes,
            "n_recovered":  n,
            "n_timed_out":  args.episodes - n,
            "mttr_mean_s":   round(mean, 2),
            "mttr_stddev_s": round(math.sqrt(variance), 2),
            "mttr_min_s":    round(min(mttrs), 2),
            "mttr_max_s":    round(max(mttrs), 2),
            "mttrs":         [round(m, 2) for m in mttrs],
        }
    else:
        summary = {
            "container":   args.container,
            "n_episodes":  args.episodes,
            "n_recovered": 0,
            "n_timed_out": args.episodes,
        }

    with open(SUMMARY_OUT, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n[episodes] done")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
