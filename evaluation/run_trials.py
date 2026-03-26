# evaluation/run_trials.py — UnderCurrent Trial Runner
#
# Runs the full evaluation experiment: for each controller × scenario × trial,
# generate fault events, feed them into the appropriate store, time the decision
# call, and save structured JSON results.
#
# CLI usage:
#   python evaluation/run_trials.py [--n 30] [--pilot] [--seed 42]
#                                   [--output evaluation/results]
#                                   [--scenarios all|id1,id2,...]
#
# Output layout:
#   evaluation/results/{controller}/{scenario_id}/trial_{n:03d}.json

import sys
import os
import json
import time
import math
import argparse
import statistics

# ── sys.path: ensure sibling packages are importable ──────────────────────────
_REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_EVAL_DIR  = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateless'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateful'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'shared'))
sys.path.insert(0, _EVAL_DIR)   # so fault_harness / baseline_controller are importable

# ── Optional tqdm ──────────────────────────────────────────────────────────────
try:
    from tqdm import tqdm
except ImportError:
    class tqdm:  # noqa: N801
        """Fallback no-op tqdm when tqdm is not installed."""
        def __init__(self, iterable=None, **kw):
            self._it = iterable
        def __iter__(self):
            return iter(self._it) if self._it is not None else iter([])
        def update(self, n=1): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

# ── Project imports ────────────────────────────────────────────────────────────
# Both tracks have a module named state_store.py; import each by explicit path.
import importlib.util as _ilu

def _load_module_from_path(reg_name: str, abs_path: str):
    """
    Import a Python module from an absolute file path and register it.

    Args:
        reg_name: the module name to use in sys.modules.
        abs_path: absolute path to the .py file.

    Returns:
        The loaded module object.
    """
    spec = _ilu.spec_from_file_location(reg_name, abs_path)
    mod  = _ilu.module_from_spec(spec)
    sys.modules[reg_name] = mod
    spec.loader.exec_module(mod)
    return mod

_sl_ss_mod  = _load_module_from_path(
    '_sl_state_store', os.path.join(_REPO_ROOT, 'stateless', 'state_store.py'))
StateStore  = _sl_ss_mod.StateStore

_sf_ss_mod          = _load_module_from_path(
    '_sf_state_store', os.path.join(_REPO_ROOT, 'stateful', 'state_store.py'))
StatefulStateStore  = _sf_ss_mod.StatefulStateStore

_sf_fsm_mod  = _load_module_from_path(
    '_sf_fsm', os.path.join(_REPO_ROOT, 'stateful', 'fsm.py'))
ContainerFSM = _sf_fsm_mod.ContainerFSM

from fault_harness import (
    FAULT_SCENARIOS, generate_events, get_scenario, list_scenarios,
)
from baseline_controller import BaselineController


# ── Import shims for cross-package name resolution ────────────────────────────
# stateless/reconcile.py and stateful/reconcile.py live in different sys.path
# entries; we must import them by their full resolved path.

def _import_module_from_path(name: str, path: str):
    """
    Dynamically import a module from a file path and register it under `name`.

    Args:
        name: the module name to register in sys.modules.
        path: absolute path to the .py file.

    Returns:
        The imported module object.
    """
    import importlib.util
    spec   = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_controllers():
    """
    Load stateless and stateful reconcile modules with correct dependency resolution.

    Both reconcile.py files use bare-name imports (e.g. 'from confidence import …').
    To ensure each loads the RIGHT confidence.py, we:
      1. Pre-load and register each track's dependencies under canonical names.
      2. Load each reconcile.py after its dependencies are registered.

    Returns:
        Tuple (sl_reconcile_module, sf_reconcile_module).
    """
    sl_dir  = os.path.join(_REPO_ROOT, 'stateless')
    sf_dir  = os.path.join(_REPO_ROOT, 'stateful')

    # ── Pre-load stateless dependencies ───────────────────────────────────────
    _load_module_from_path(
        'confidence',   os.path.join(sl_dir, 'confidence.py'))
    _load_module_from_path(
        'state_store',  os.path.join(sl_dir, 'state_store.py'))

    sl_mod = _import_module_from_path(
        '_sl_reconcile_loaded', os.path.join(sl_dir, 'reconcile.py'))

    # ── Pre-load stateful dependencies ────────────────────────────────────────
    # Note: stateful/reconcile.py imports 'confidence', 'actions', 'fsm'
    # We must re-register 'confidence' as the stateful one BEFORE loading sf reconcile.
    _load_module_from_path(
        'confidence',   os.path.join(sf_dir, 'confidence.py'))
    _load_module_from_path(
        'state_store',  os.path.join(sf_dir, 'state_store.py'))
    _load_module_from_path(
        'actions',      os.path.join(sf_dir, 'actions.py'))
    _load_module_from_path(
        'fsm',          os.path.join(sf_dir, 'fsm.py'))

    sf_mod = _import_module_from_path(
        '_sf_reconcile_loaded', os.path.join(sf_dir, 'reconcile.py'))

    return sl_mod, sf_mod


# ── Single trial runner ───────────────────────────────────────────────────────

def run_single_trial(controller_name: str, scenario: dict,
                     trial_idx: int, seed: int,
                     sl_reconcile, sf_reconcile) -> dict:
    """
    Execute one complete trial: inject events, time the decision call, record result.

    For cascading scenarios (multi-phase), the function runs one reconcile cycle
    per phase and accumulates all decisions. The 'correct' flag checks whether
    the sequence of actions matches the scenario's expected_sequence field.

    For non-cascading scenarios, 'correct' is True if the first non-no_action
    decision (or any decision for no_action scenarios) matches expected_action.

    Args:
        controller_name: one of 'stateless', 'stateful', 'baseline'.
        scenario:        scenario dict from FAULT_SCENARIOS.
        trial_idx:       zero-based trial index within the run.
        seed:            RNG seed for this trial (base_seed + trial_idx).
        sl_reconcile:    loaded stateless reconcile module.
        sf_reconcile:    loaded stateful reconcile module.

    Returns:
        Trial result dict suitable for JSON serialization and metric computation.
    """
    category  = scenario["category"]
    track     = scenario["track"]

    # ── Generate events ────────────────────────────────────────────────────────
    # Use current time as base so events fall within the StateStore's sliding
    # window (events older than window_seconds from now() are pruned).
    # trial_idx offset is small (0.1s) to keep events within the 60s window
    # while still giving each trial slightly different timestamps.
    base_time  = time.time() - 5.0 - trial_idx * 0.01
    raw_events = generate_events(scenario, seed=seed, jitter=True,
                                 base_time=base_time)

    # ── Normalise to phases ────────────────────────────────────────────────────
    # Cascading scenarios: raw_events is a list of event lists (one per phase).
    # Single/compound/intermittent scenarios: one event list, but if
    # expected_sequence has multiple steps (e.g. vfs_errors_01 cycles through
    # flush→checkpoint), we run that many reconcile cycles on the SAME store
    # so the FSM can advance between cycles.
    n_cycles = len(scenario.get("expected_sequence", []))

    if category == "cascading":
        # Each phase injects new events, then reconciles once
        phases = [(evs, True) for evs in raw_events]
    elif n_cycles > 1:
        # Multi-step non-cascading: inject all events in cycle 0, then run
        # additional reconcile cycles on the same store (no new events) so
        # the FSM can advance through its state sequence.
        phases = [(raw_events if idx == 0 else [], idx == 0)
                  for idx in range(n_cycles)]
    else:
        phases = [(raw_events, True)]

    all_decisions        = []
    total_latency_ns     = 0
    injected_event_count = len(raw_events) if category != "cascading" else \
                           sum(len(p) for p in raw_events)

    import io as _io

    # ── Initialise stores ──────────────────────────────────────────────────────
    if track in ("S", "both") or controller_name in ("stateless", "baseline"):
        s_store = StateStore(window_seconds=60)
    if track in ("F", "both") or controller_name == "stateful":
        f_store = StatefulStateStore(window_seconds=60)
        fsm     = ContainerFSM()

    # ── Per-phase reconcile loop ───────────────────────────────────────────────
    for phase_idx, (phase_events, inject) in enumerate(phases):
        # Feed events into the appropriate store (only when inject=True)
        if inject:
            for ev in phase_events:
                if controller_name == "stateless":
                    s_store.record(ev)
                elif controller_name == "stateful":
                    f_store.record(ev)
                elif controller_name == "baseline":
                    if track == "F":
                        f_store.record(ev)
                    else:
                        s_store.record(ev)

        # ── Time the decision call ─────────────────────────────────────────────
        t0 = time.perf_counter_ns()

        if controller_name == "stateless":
            old_stdout = sys.stdout
            sys.stdout = _io.StringIO()
            try:
                phase_decisions = sl_reconcile.reconcile(s_store, shadow=False)
            finally:
                sys.stdout = old_stdout

        elif controller_name == "stateful":
            old_stdout = sys.stdout
            sys.stdout = _io.StringIO()
            try:
                phase_decisions = sf_reconcile.reconcile(f_store, fsm, shadow=False)
            finally:
                sys.stdout = old_stdout

        elif controller_name == "baseline":
            ctrl_b = BaselineController()
            if track == "F":
                phase_decisions = ctrl_b.decide_all(f_store)
            else:
                phase_decisions = ctrl_b.decide_all(s_store)

        t1 = time.perf_counter_ns()
        total_latency_ns += (t1 - t0)

        # Annotate each decision with phase index
        for d in phase_decisions:
            d_copy = dict(d)
            d_copy["phase"] = phase_idx
            all_decisions.append(d_copy)

    # ── Correctness evaluation ─────────────────────────────────────────────────
    correct = _evaluate_correctness(scenario, all_decisions)

    return {
        "scenario_id":          scenario["id"],
        "controller":           controller_name,
        "trial_idx":            trial_idx,
        "seed":                 seed,
        "decisions":            all_decisions,
        "expected_action":      scenario["expected_action"],
        "expected_sequence":    scenario["expected_sequence"],
        "correct":              correct,
        "decision_latency_ns":  total_latency_ns,
        "injected_event_count": injected_event_count,
        "timestamp":            time.time(),
    }


def _evaluate_correctness(scenario: dict, decisions: list) -> bool:
    """
    Determine whether the controller's decisions match the expected outcome.

    For cascading scenarios: the sequence of per-phase primary actions must
    match expected_sequence (one action per phase, ignoring no_action decisions
    that correspond to containers not involved in the fault).

    For non-cascading scenarios: the first non-no_action decision action must
    equal expected_action. If expected_action is 'no_action', all decisions
    must be no_action.

    Args:
        scenario:  scenario dict with expected_action and expected_sequence.
        decisions: list of decision dicts from all phases.

    Returns:
        True if the decisions match the expected outcome; False otherwise.
    """
    expected_action   = scenario["expected_action"]
    expected_sequence = scenario["expected_sequence"]
    category          = scenario["category"]
    n_cycles          = len(expected_sequence)

    if category == "cascading" or n_cycles > 1:
        # Group by phase and match each phase's primary action to expected_sequence
        max_phase = max((d.get("phase", 0) for d in decisions), default=0)
        phase_actions = []
        for p in range(max_phase + 1):
            phase_decisions = [d for d in decisions if d.get("phase") == p]
            action = _primary_action(phase_decisions)
            phase_actions.append(action)
        return phase_actions == expected_sequence

    else:
        # Single-cycle: check the primary action across all decisions
        primary = _primary_action(decisions)
        if expected_action == "no_action":
            return primary == "no_action"
        return primary == expected_action


def _primary_action(decisions: list) -> str:
    """
    Extract the most significant action from a list of decision dicts.

    Priority order (highest first): escalate, reschedule, checkpoint_and_restart,
    restart, flush_io_queue, no_action. Returns 'no_action' if decisions is empty.

    Args:
        decisions: list of decision dicts.

    Returns:
        The highest-priority action string found.
    """
    _PRIORITY = {
        "escalate":               6,
        "reschedule":             5,
        "checkpoint_and_restart": 4,
        "restart":                3,
        "flush_io_queue":         2,
        "no_action":              1,
    }
    best = "no_action"
    best_p = 0
    for d in decisions:
        a = d.get("action", "no_action")
        p = _PRIORITY.get(a, 0)
        if p > best_p:
            best   = a
            best_p = p
    return best


# ── Pilot statistics ──────────────────────────────────────────────────────────

def compute_pilot_stats(pilot_results: list) -> dict:
    """
    Compute per-scenario summary statistics from pilot trial results.

    Calculates the standard deviation of correctness rate and decision latency
    across trials, then applies the Wald formula to estimate the sample size
    required for a 95% confidence interval with ±5% margin of error.

    Formula: n = (z_alpha * sigma / margin_of_error)^2
    where z_alpha = 1.96 (two-tailed 95% CI) and margin_of_error = 0.05.

    Args:
        pilot_results: list of trial result dicts from the pilot run.

    Returns:
        Dict mapping scenario_id → stats dict with keys:
          correctness_std, latency_std_ns, n_required_correctness,
          n_required_latency, n_required (max of the two).
    """
    Z_ALPHA = 1.96
    MOE     = 0.05   # ±5%

    # Group by scenario_id
    by_scenario = {}
    for r in pilot_results:
        sid = r["scenario_id"]
        by_scenario.setdefault(sid, []).append(r)

    stats = {}
    for sid, trials in by_scenario.items():
        correctness  = [1.0 if t["correct"] else 0.0 for t in trials]
        latencies_ns = [t["decision_latency_ns"] for t in trials]

        if len(correctness) < 2:
            corr_std = 0.0
            lat_std  = 0.0
        else:
            corr_std = statistics.stdev(correctness)
            lat_std  = statistics.stdev(latencies_ns)

        # Normalise latency std to [0,1] range using a 10ms reference
        lat_std_norm = lat_std / 10_000_000  # 10 ms in ns

        n_corr = math.ceil((Z_ALPHA * corr_std / max(MOE, 1e-9)) ** 2) if corr_std > 0 else 1
        n_lat  = math.ceil((Z_ALPHA * lat_std_norm / max(MOE, 1e-9)) ** 2) if lat_std_norm > 0 else 1

        stats[sid] = {
            "n_trials":                len(trials),
            "correctness_mean":        round(statistics.mean(correctness), 4),
            "correctness_std":         round(corr_std, 4),
            "latency_mean_ns":         round(statistics.mean(latencies_ns), 1),
            "latency_std_ns":          round(lat_std, 1),
            "n_required_correctness":  n_corr,
            "n_required_latency":      n_lat,
            "n_required":              max(n_corr, n_lat),
        }

    return stats


# ── Main trial runner ─────────────────────────────────────────────────────────

def run_all_trials(n: int, scenarios: list, output_dir: str,
                   base_seed: int, sl_reconcile, sf_reconcile,
                   controller_filter: list = None) -> list:
    """
    Run the full N × scenario × controller trial matrix and persist results to disk.

    Creates output directories automatically. Each trial is written as a
    separate JSON file at:
      {output_dir}/{controller}/{scenario_id}/trial_{trial_idx:03d}.json

    Args:
        n:                 number of trials per scenario per controller.
        scenarios:         list of scenario dicts to test.
        output_dir:        root directory for trial result files.
        base_seed:         base RNG seed; trial i uses seed = base_seed + i.
        sl_reconcile:      loaded stateless reconcile module.
        sf_reconcile:      loaded stateful reconcile module.
        controller_filter: optional list of controller names to restrict to;
                           defaults to all three controllers.

    Returns:
        Flat list of all trial result dicts.
    """
    controllers = controller_filter or ["stateless", "stateful", "baseline"]
    all_results = []

    total_work = len(controllers) * len(scenarios) * n
    pbar = tqdm(total=total_work, desc="Running trials", unit="trial")

    for ctrl_name in controllers:
        for scenario in scenarios:
            sid       = scenario["id"]
            track     = scenario["track"]

            # Skip controller/track mismatches to avoid meaningless results
            if ctrl_name == "stateless" and track == "F":
                pbar.update(n)
                continue
            if ctrl_name == "stateful" and track == "S":
                pbar.update(n)
                continue

            out_dir = os.path.join(output_dir, ctrl_name, sid)
            os.makedirs(out_dir, exist_ok=True)

            for trial_idx in range(n):
                seed   = base_seed + trial_idx
                result = run_single_trial(
                    ctrl_name, scenario, trial_idx, seed,
                    sl_reconcile, sf_reconcile,
                )
                out_path = os.path.join(out_dir, f"trial_{trial_idx:03d}.json")
                with open(out_path, "w") as fh:
                    json.dump(result, fh, indent=2, default=str)

                all_results.append(result)
                pbar.update(1)

    pbar.close()
    return all_results


def print_summary_table(all_results: list) -> None:
    """
    Print a human-readable summary table of trial results to stdout.

    Shows per-controller × per-scenario: number of trials, correctness rate,
    and mean decision latency in microseconds.

    Args:
        all_results: flat list of all trial result dicts.
    """
    from collections import defaultdict
    summary = defaultdict(lambda: {"correct": 0, "total": 0, "latency_ns": []})

    for r in all_results:
        key = (r["controller"], r["scenario_id"])
        summary[key]["total"]     += 1
        summary[key]["correct"]   += int(r["correct"])
        summary[key]["latency_ns"].append(r["decision_latency_ns"])

    col_widths = [16, 32, 8, 12, 14]
    header     = (f"{'Controller':<{col_widths[0]}} "
                  f"{'Scenario':<{col_widths[1]}} "
                  f"{'Trials':>{col_widths[2]}} "
                  f"{'Correct%':>{col_widths[3]}} "
                  f"{'AvgLatency(µs)':>{col_widths[4]}}")
    sep = "-" * len(header)

    print(f"\n{sep}")
    print(header)
    print(sep)

    for (ctrl, sid), data in sorted(summary.items()):
        pct     = round(100.0 * data["correct"] / data["total"], 1) if data["total"] else 0.0
        lats    = data["latency_ns"]
        avg_lat = round(statistics.mean(lats) / 1000, 1) if lats else 0.0
        print(f"{ctrl:<{col_widths[0]}} "
              f"{sid:<{col_widths[1]}} "
              f"{data['total']:>{col_widths[2]}} "
              f"{pct:>{col_widths[3]}.1f}% "
              f"{avg_lat:>{col_widths[4]}.1f}")

    print(sep)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    """
    Entry point for the trial runner CLI.

    Parses arguments, loads controller modules, runs trials (or pilot), and
    prints a summary table. In pilot mode, also prints required sample size.
    """
    parser = argparse.ArgumentParser(
        description="UnderCurrent evaluation trial runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--n",         type=int,   default=30,
                        help="Number of trials per scenario per controller (default: 30)")
    parser.add_argument("--pilot",     action="store_true",
                        help="Run in pilot mode (N=10) and compute required sample size")
    parser.add_argument("--seed",      type=int,   default=42,
                        help="Base RNG seed (default: 42)")
    parser.add_argument("--output",    type=str,   default="evaluation/results",
                        help="Root directory for result files (default: evaluation/results)")
    parser.add_argument("--scenarios", type=str,   default="all",
                        help="Comma-separated scenario IDs, or 'all' (default: all)")
    parser.add_argument("--controllers", type=str, default="all",
                        help="Comma-separated controller names or 'all' (default: all)")
    args = parser.parse_args()

    # ── Load controllers ───────────────────────────────────────────────────────
    sl_reconcile, sf_reconcile = _load_controllers()

    # ── Resolve scenarios ──────────────────────────────────────────────────────
    if args.scenarios == "all":
        scenarios = list(FAULT_SCENARIOS)
    else:
        ids       = [s.strip() for s in args.scenarios.split(",")]
        scenarios = [get_scenario(sid) for sid in ids]

    # ── Resolve controllers ────────────────────────────────────────────────────
    if args.controllers == "all":
        controller_filter = None
    else:
        controller_filter = [c.strip() for c in args.controllers.split(",")]

    # ── Pilot mode ─────────────────────────────────────────────────────────────
    n = 10 if args.pilot else args.n

    print(f"UnderCurrent trial runner")
    print(f"  n={n}  seed={args.seed}  pilot={args.pilot}")
    print(f"  scenarios: {len(scenarios)}  output: {args.output}")

    os.makedirs(args.output, exist_ok=True)

    # ── Run trials ─────────────────────────────────────────────────────────────
    all_results = run_all_trials(
        n=n,
        scenarios=scenarios,
        output_dir=args.output,
        base_seed=args.seed,
        sl_reconcile=sl_reconcile,
        sf_reconcile=sf_reconcile,
        controller_filter=controller_filter,
    )

    print_summary_table(all_results)

    # ── Pilot report ───────────────────────────────────────────────────────────
    if args.pilot and all_results:
        stats = compute_pilot_stats(all_results)
        max_n = max(v["n_required"] for v in stats.values()) if stats else n

        print("\nPilot per-scenario statistics:")
        col = [32, 8, 10, 12, 14]
        hdr = (f"  {'Scenario':<{col[0]}} {'N':>{col[1]}} "
               f"{'Correct%':>{col[2]}} {'LatStd(ns)':>{col[3]}} {'N_required':>{col[4]}}")
        print(hdr)
        print("  " + "-" * (sum(col) + len(col)))
        for sid, sv in sorted(stats.items()):
            print(f"  {sid:<{col[0]}} {sv['n_trials']:>{col[1]}} "
                  f"{sv['correctness_mean']*100:>{col[2]}.1f}% "
                  f"{sv['latency_std_ns']:>{col[3]}.0f} "
                  f"{sv['n_required']:>{col[4]}}")

        print(f"\nPilot complete. Suggested N={max_n} for 95% CI with ±5% margin of error")

    total_written = len(all_results)
    print(f"\nWrote {total_written} trial result files to {args.output}/")


if __name__ == "__main__":
    main()
