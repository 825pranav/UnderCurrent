#!/usr/bin/env python3
"""
Overhead benchmark for Day 5.

Two measurements:
  1. WASM policy_check() steady-state latency (N=10,000 calls, post-JIT warmup)
  2. Shadow execution overhead: reconcile_both() vs reconcile() wall time

Results written to: results/overhead_benchmark.json
"""
import json
import math
import os
import sys
import time

# Ensure repo root is on the path regardless of invocation directory.
_HERE    = os.path.dirname(os.path.abspath(__file__))
_REPO    = os.path.dirname(_HERE)
_STATEFUL = os.path.join(_REPO, "stateful")
# stateful/ modules use bare-name imports — must be on sys.path
sys.path.insert(0, _STATEFUL)
sys.path.insert(0, _REPO)

from fsm import ContainerFSM
from state_store import StatefulStateStore
from reconcile import reconcile, reconcile_both
from wasm_executor import policy_check

# ── helpers ────────────────────────────────────────────────────────────────────

def _stats(samples: list) -> dict:
    n = len(samples)
    if n == 0:
        return {"n": 0, "mean_us": 0.0, "stddev_us": 0.0, "min_us": 0.0,
                "max_us": 0.0, "p50_us": 0.0, "p95_us": 0.0, "p99_us": 0.0}
    s = sorted(samples)
    mean = sum(s) / n
    var  = sum((x - mean) ** 2 for x in s) / n
    def pct(p):
        idx = max(0, min(n - 1, int(math.ceil(p / 100 * n)) - 1))
        return round(s[idx], 3)
    return {
        "n":         n,
        "mean_us":   round(mean, 3),
        "stddev_us": round(math.sqrt(var), 3),
        "min_us":    round(s[0], 3),
        "max_us":    round(s[-1], 3),
        "p50_us":    pct(50),
        "p95_us":    pct(95),
        "p99_us":    pct(99),
    }


def _inject_fault_events(store: StatefulStateStore) -> None:
    """Push 8 blk_io_latency events into store (same pattern as run_episodes.py)."""
    import random
    now = time.time()
    for i in range(8):
        store.record({
            "container":  "benchmark",
            "pid":        90000 + i,
            "event":      "blk_io_latency",
            "latency_us": 50_000 + i * 500,
            "time":       now + i * 0.01,
            "node_type":  "F",
        })


# ── Benchmark 1: WASM policy_check() latency ──────────────────────────────────

def bench_wasm(n: int = 10_000) -> dict:
    """
    N=10,000 calls to policy_check() after JIT warmup.
    Use the hot path: checkpoint_and_restart from Audited state (score=0.85).
    This is the most complex allowed action in the policy grammar.
    """
    print(f"[bench] WASM policy_check: {n:,} calls (post-warmup)...")

    # Warmup: 200 calls to ensure JIT is fully compiled.
    for _ in range(200):
        policy_check("checkpoint_and_restart", "Audited", 0.85)

    # Benchmark all four action types for variety.
    cases = [
        ("no_action",              "Healthy",   0.1),
        ("flush_io_queue",         "Degraded",  0.65),
        ("checkpoint_and_restart", "Audited",   0.85),
        ("escalate",               "Repairing", 0.96),
    ]

    all_ns: list = []
    per_action: dict = {}

    calls_per_case = n // len(cases)
    for action, state, score in cases:
        samples_ns = []
        for _ in range(calls_per_case):
            t0 = time.perf_counter_ns()
            policy_check(action, state, score)
            samples_ns.append(time.perf_counter_ns() - t0)
        all_ns.extend(samples_ns)
        per_action[action] = _stats([x / 1_000 for x in samples_ns])

    total_n = len(all_ns)
    overall = _stats([x / 1_000 for x in all_ns])

    print(f"[bench] WASM: n={total_n:,}  mean={overall['mean_us']:.2f}µs  "
          f"p95={overall['p95_us']:.2f}µs  p99={overall['p99_us']:.2f}µs")

    return {
        "description": "WASM policy_check() steady-state latency (post JIT warmup, N=10000)",
        "overall": overall,
        "per_action": per_action,
    }


# ── Benchmark 2: Shadow execution overhead ─────────────────────────────────────

def bench_shadow(n: int = 2_000) -> dict:
    """
    Compare reconcile() (single-path) vs reconcile_both() (real + shadow).
    Overhead = reconcile_both() wall time − reconcile() wall time.

    N iterations; each iteration uses a fresh StateStore populated with
    fault events so the FSM has decisions to make (not just no_action).
    """
    print(f"[bench] Shadow overhead: {n:,} iterations each (real-only vs both)...")

    single_us: list = []
    dual_us:   list = []

    _devnull = open(os.devnull, "w")
    for _ in range(n):
        store = StatefulStateStore(window_seconds=120)
        _inject_fault_events(store)

        fsm_s = ContainerFSM()
        t0 = time.perf_counter_ns()
        _old, sys.stdout = sys.stdout, _devnull
        reconcile(store, fsm_s, shadow=False)
        sys.stdout = _old
        single_us.append((time.perf_counter_ns() - t0) / 1_000)

        fsm_d = ContainerFSM()
        t0 = time.perf_counter_ns()
        _old, sys.stdout = sys.stdout, _devnull
        reconcile_both(store, fsm_d)
        sys.stdout = _old
        dual_us.append((time.perf_counter_ns() - t0) / 1_000)
    _devnull.close()

    s_stats  = _stats(single_us)
    d_stats  = _stats(dual_us)
    overhead = round(d_stats["mean_us"] - s_stats["mean_us"], 3)
    overhead_pct = round(overhead / max(s_stats["mean_us"], 0.001) * 100, 1)

    print(f"[bench] single={s_stats['mean_us']:.1f}µs  "
          f"dual={d_stats['mean_us']:.1f}µs  "
          f"shadow-overhead={overhead:.1f}µs ({overhead_pct}%)")

    return {
        "description": "reconcile_both() vs reconcile() wall time (N=2000, fault-loaded store)",
        "single_path": s_stats,
        "dual_path":   d_stats,
        "shadow_overhead_us":  overhead,
        "shadow_overhead_pct": overhead_pct,
    }


# ── Benchmark 3: Pure Python policy_check() (fallback) for comparison ─────────

def bench_python_policy(n: int = 10_000) -> dict:
    """
    Pure Python equivalent of the WASM policy check for direct comparison.
    Uses same logic as the no_wasm fallback in actions.py.
    """
    print(f"[bench] Python policy_check equivalent: {n:,} calls...")

    REPAIR_THRESHOLD   = 0.80
    FLUSH_THRESHOLD    = 0.50
    ESCALATE_THRESHOLD = 0.95
    FSM_ALLOW = {
        "no_action":              {"Healthy", "Degraded", "Audited", "Repairing", "Recovered"},
        "flush_io_queue":         {"Degraded", "Audited"},
        "checkpoint_and_restart": {"Audited"},
        "escalate":               {"Repairing"},
    }

    def _python_policy(action, fsm_state, score):
        allowed_states = FSM_ALLOW.get(action, set())
        return fsm_state in allowed_states

    cases = [
        ("checkpoint_and_restart", "Audited", 0.85),
        ("flush_io_queue",         "Degraded", 0.65),
        ("no_action",              "Healthy", 0.1),
        ("escalate",               "Repairing", 0.96),
    ]
    samples_ns = []
    calls_per_case = n // len(cases)
    for action, state, score in cases:
        for _ in range(calls_per_case):
            t0 = time.perf_counter_ns()
            _python_policy(action, state, score)
            samples_ns.append(time.perf_counter_ns() - t0)

    result = _stats([x / 1_000 for x in samples_ns])
    print(f"[bench] Python: n={len(samples_ns):,}  mean={result['mean_us']:.3f}µs  "
          f"p99={result['p99_us']:.3f}µs")
    return {
        "description": "Pure Python FSM state lookup (equivalent policy logic, for WASM comparison)",
        "overall": result,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    out_file = os.path.join(_REPO, "results", "overhead_benchmark.json")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)

    print("=" * 60)
    print("UnderCurrent Day-5 Overhead Benchmark")
    print("=" * 60)

    results = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {
            "python": sys.version.split()[0],
        },
    }

    results["wasm_latency"]   = bench_wasm(n=10_000)
    results["shadow_overhead"] = bench_shadow(n=2_000)
    results["python_policy"]  = bench_python_policy(n=10_000)

    # Compute WASM vs Python ratio
    wasm_mean   = results["wasm_latency"]["overall"]["mean_us"]
    python_mean = results["python_policy"]["overall"]["mean_us"]
    if python_mean > 0:
        results["wasm_vs_python_ratio"] = round(wasm_mean / python_mean, 1)
    else:
        results["wasm_vs_python_ratio"] = None

    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    w = results["wasm_latency"]["overall"]
    s = results["shadow_overhead"]
    p = results["python_policy"]["overall"]
    print(f"WASM policy_check():    mean={w['mean_us']:.2f}µs  p99={w['p99_us']:.2f}µs  (n={w['n']:,})")
    print(f"Python equivalent:      mean={p['mean_us']:.3f}µs  p99={p['p99_us']:.3f}µs  (n={p['n']:,})")
    print(f"WASM/Python ratio:      {results['wasm_vs_python_ratio']}×")
    print(f"Shadow overhead:        {s['shadow_overhead_us']:.1f}µs above single-path")
    print(f"                        ({s['shadow_overhead_pct']}% of single-path cost)")
    print(f"Results: {out_file}")


if __name__ == "__main__":
    main()
