# evaluation/sensitivity.py — UnderCurrent Parameter Sensitivity Analysis
# (trial-based; results cited in paper — outputs evaluation/results/sensitivity_results.csv)
#
# See also: shared/sensitivity.py — lightweight trace-replay variant for local exploration.
#
# Sweeps key controller parameters to characterise how performance degrades
# or improves as each parameter moves away from its default value.
#
# Three sweep dimensions:
#   1. Confidence threshold (stateless RESTART_THRESHOLD): [0.60, 0.70, 0.80, 0.90, 0.95]
#   2. History window size (both tracks): [15, 30, 60, 120, 240] seconds
#   3. FSM flush threshold (stateful FLUSH_THRESHOLD): [0.30, 0.40, 0.50, 0.60, 0.70]
#
# CLI usage:
#   python evaluation/sensitivity.py [--n 20] [--seed 42] [--output evaluation/results]

import sys
import os
import json
import csv
import time
import math
import statistics
import argparse
from collections import defaultdict

# ── sys.path: ensure sibling packages are importable ──────────────────────────
_REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_EVAL_DIR  = os.path.dirname(os.path.abspath(__file__))
# stateless FIRST so bare 'state_store' resolves to StateStore, not StatefulStateStore
sys.path.insert(0, os.path.join(_REPO_ROOT, 'shared'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateful'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateless'))
sys.path.insert(0, _EVAL_DIR)

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

from fault_harness import FAULT_SCENARIOS, generate_events


# ── Sweep parameter definitions ───────────────────────────────────────────────

RESTART_THRESHOLD_VALUES  = [0.60, 0.70, 0.80, 0.90, 0.95]
WINDOW_SECONDS_VALUES     = [15, 30, 60, 120, 240]
FLUSH_THRESHOLD_VALUES    = [0.30, 0.40, 0.50, 0.60, 0.70]


# ── Parametric stateless decision engine ──────────────────────────────────────

def _stateless_decide(container: str, s_store,
                       restart_threshold: float,
                       reschedule_threshold: float = 0.95) -> dict:
    """
    Stateless decision engine with configurable threshold parameters.

    Mirrors stateless/reconcile.py's _decide() but accepts threshold values
    as arguments rather than using module-level constants, enabling sweep tests.

    Args:
        container:            name of the container.
        s_store:              StateStore with current events.
        restart_threshold:    score >= this triggers restart.
        reschedule_threshold: score >= this triggers reschedule.

    Returns:
        Decision dict with action, score, reason, dag_pattern, kernel_signals.
    """
    import confidence as sl_conf

    score    = sl_conf.compute_confidence(container, s_store)
    failures = s_store.get_failures(container)
    signals  = sorted({e.get("event", "") for e in failures} - {""})

    if score >= reschedule_threshold:
        action      = "reschedule"
        dag_pattern = "process_failure"
        reason      = f"score {score} >= reschedule threshold {reschedule_threshold}"
    elif score >= restart_threshold:
        action      = "restart"
        dag_pattern = "network_degraded"
        reason      = f"score {score} >= restart threshold {restart_threshold}"
    else:
        action      = "no_action"
        dag_pattern = "nominal"
        reason      = f"score {score} below restart threshold {restart_threshold}"

    return {
        "container":      container,
        "score":          score,
        "action":         action,
        "reason":         reason,
        "mode":           "real",
        "dag_pattern":    dag_pattern,
        "kernel_signals": signals,
        "timestamp":      time.time(),
    }


# ── Parametric stateful decision engine ──────────────────────────────────────

def _stateful_decide_all(f_store, fsm,
                          flush_threshold: float,
                          repair_threshold: float = 0.80,
                          escalate_threshold: float = 0.95) -> list:
    """
    Stateful FSM-gated decision engine with configurable threshold parameters.

    Mirrors stateful/reconcile.py's _decide() applied to all containers in
    the store, but accepts threshold values as arguments.

    Args:
        f_store:            StatefulStateStore with current events.
        fsm:                ContainerFSM instance (may be mutated).
        flush_threshold:    score >= this triggers flush_io_queue.
        repair_threshold:   score >= this triggers checkpoint_and_restart.
        escalate_threshold: score >= this triggers escalate.

    Returns:
        List of decision dicts, one per container.
    """
    import confidence as sf_conf

    decisions = []
    for container in f_store.get_all_containers():
        score    = sf_conf.compute_confidence(container, f_store)
        failures = f_store.get_failures(container)
        signals  = sorted({e.get("event", "") for e in failures} - {""})

        if score >= escalate_threshold:
            action      = "escalate"
            dag_pattern = "io_degradation_critical"
            reason      = f"score {score} >= escalate threshold {escalate_threshold}"
        elif score >= repair_threshold:
            action      = "checkpoint_and_restart"
            dag_pattern = "io_degradation_major"
            reason      = f"score {score} >= repair threshold {repair_threshold}"
        elif score >= flush_threshold:
            action      = "flush_io_queue"
            dag_pattern = "io_degradation_minor"
            reason      = f"score {score} >= flush threshold {flush_threshold}"
        else:
            action      = "no_action"
            dag_pattern = "nominal"
            reason      = f"score {score} below all thresholds"

        # FSM gating
        blocked_reason = None
        if action != "no_action":
            current = fsm.state(container)
            if score >= flush_threshold and current in ("Healthy", "Recovered"):
                t = fsm.apply(container, "degrade")
                if t:
                    current = fsm.state(container)

            if action == "checkpoint_and_restart" and current != "Audited":
                action         = "flush_io_queue"
                blocked_reason = f"deferred: FSM={current}"
                reason         = blocked_reason

            if action == "flush_io_queue" and current == "Degraded":
                fsm.apply(container, "audit")
                current = fsm.state(container)

            if action == "checkpoint_and_restart" and current == "Audited":
                fsm.apply(container, "approve_repair")

            if action == "escalate" and current not in ("Degraded", "Audited", "Repairing"):
                action         = "flush_io_queue"
                blocked_reason = f"escalate blocked: FSM={current}"
                reason         = blocked_reason

        decisions.append({
            "container":      container,
            "score":          score,
            "action":         action,
            "reason":         reason,
            "mode":           "real",
            "dag_pattern":    dag_pattern,
            "kernel_signals": signals,
            "blocked_reason": blocked_reason,
            "timestamp":      time.time(),
        })

    return decisions


# ── Trial runner helpers ───────────────────────────────────────────────────────

def _primary_action(decisions: list) -> str:
    """Return the highest-priority action from a list of decision dicts."""
    _P = {
        "escalate": 6, "reschedule": 5, "checkpoint_and_restart": 4,
        "restart": 3, "flush_io_queue": 2, "no_action": 1,
    }
    best, best_p = "no_action", 0
    for d in decisions:
        a = d.get("action", "no_action")
        p = _P.get(a, 0)
        if p > best_p:
            best, best_p = a, p
    return best


def _eval_correct(scenario: dict, decisions: list) -> bool:
    """Evaluate trial correctness (same logic as run_trials.py)."""
    expected_action   = scenario["expected_action"]
    expected_sequence = scenario["expected_sequence"]
    category          = scenario["category"]
    n_cycles          = len(expected_sequence)
    _P = {
        "escalate": 6, "reschedule": 5, "checkpoint_and_restart": 4,
        "restart": 3, "flush_io_queue": 2, "no_action": 1,
    }

    def _primary_phase(decs):
        best, best_p = "no_action", 0
        for d in decs:
            a = d.get("action", "no_action")
            p = _P.get(a, 0)
            if p > best_p:
                best, best_p = a, p
        return best

    if category == "cascading" or n_cycles > 1:
        max_phase = max((d.get("phase", 0) for d in decisions), default=0)
        seq = [_primary_phase([d for d in decisions if d.get("phase") == p])
               for p in range(max_phase + 1)]
        return seq == expected_sequence
    else:
        primary = _primary_action(decisions)
        if expected_action == "no_action":
            return primary == "no_action"
        return primary == expected_action


def _confusion_metrics(trials: list) -> dict:
    """Compute macro-averaged precision, recall, F1 over a list of trial dicts."""
    tp = fp = fn = tn = 0
    for t in trials:
        expected = t.get("expected_action", "no_action")
        action   = _primary_action(t.get("decisions", []))
        acted    = action != "no_action"
        exp      = expected != "no_action"
        if acted and exp:     tp += 1
        elif not acted and not exp: tn += 1
        elif acted and not exp:     fp += 1
        else:                       fn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    return {
        "precision": round(precision, 4),
        "recall":    round(recall,    4),
        "f1":        round(f1,        4),
    }


def _unsafe_suppression_rate(trials: list) -> float:
    """Compute unsafe action suppression rate across trials."""
    suppressed = 0
    total      = 0
    for t in trials:
        for d in t.get("decisions", []):
            total += 1
            if d.get("blocked_reason"):
                suppressed += 1
    return round(suppressed / total, 4) if total > 0 else 0.0


# ── Sweep 1: Confidence threshold (stateless RESTART_THRESHOLD) ───────────────

def sweep_restart_threshold(n: int, base_seed: int) -> list:
    """
    Sweep the stateless RESTART_THRESHOLD over [0.60, 0.70, 0.80, 0.90, 0.95].

    For each threshold value, runs all S-track scenarios N times and records
    precision and recall. A lower threshold triggers more actions (higher recall,
    lower precision); a higher threshold is more conservative.

    Args:
        n:         number of trials per threshold × scenario.
        base_seed: base RNG seed.

    Returns:
        List of result row dicts with fields:
          parameter, value, controller, fault_type, precision, recall, f1.
    """
    import importlib.util as _ilu0
    _sl_ss0 = _ilu0.spec_from_file_location('sl_state_store0',
                  os.path.join(_REPO_ROOT, 'stateless', 'state_store.py'))
    _sl_mod0 = _ilu0.module_from_spec(_sl_ss0); _sl_ss0.loader.exec_module(_sl_mod0)
    StateStore = _sl_mod0.StateStore

    s_scenarios = [s for s in FAULT_SCENARIOS if s["track"] in ("S", "both")]
    results     = []
    total_work  = len(RESTART_THRESHOLD_VALUES) * len(s_scenarios) * n
    pbar = tqdm(total=total_work, desc="Threshold sweep", unit="trial")

    for threshold in RESTART_THRESHOLD_VALUES:
        for scenario in s_scenarios:
            sid       = scenario["id"]
            trials    = []

            for trial_idx in range(n):
                seed      = base_seed + trial_idx
                base_time = time.time() - 5.0 - trial_idx * 0.01
                events    = generate_events(scenario, seed=seed, jitter=True,
                                            base_time=base_time)
                if scenario["category"] == "cascading":
                    phases = events
                else:
                    phases = [events]

                all_decisions = []
                for phase_idx, phase_events in enumerate(phases):
                    s_store = StateStore(window_seconds=60)
                    for ev in phase_events:
                        s_store.record(ev)
                    for container in s_store.get_all_containers():
                        d = _stateless_decide(
                            container, s_store,
                            restart_threshold=threshold,
                            reschedule_threshold=0.95,
                        )
                        d["phase"] = phase_idx
                        all_decisions.append(d)

                correct = _eval_correct(scenario, all_decisions)
                trials.append({
                    "scenario_id": sid,
                    "expected_action": scenario["expected_action"],
                    "decisions": all_decisions,
                    "correct": correct,
                })
                pbar.update(1)

            metrics = _confusion_metrics(trials)
            results.append({
                "parameter":  "restart_threshold",
                "value":      threshold,
                "controller": "stateless",
                "fault_type": sid,
                "precision":  metrics["precision"],
                "recall":     metrics["recall"],
                "f1":         metrics["f1"],
            })

    pbar.close()
    return results


# ── Sweep 2: History window (both tracks) ─────────────────────────────────────

def sweep_window_seconds(n: int, base_seed: int) -> list:
    """
    Sweep the sliding window duration over [15, 30, 60, 120, 240] seconds
    for both S-track and F-track scenarios.

    A shorter window may miss slow-onset faults; a longer window may retain
    stale signals and inflate scores after recovery.

    Args:
        n:         number of trials per window × scenario.
        base_seed: base RNG seed.

    Returns:
        List of result row dicts with fields:
          parameter, value, controller, fault_type, precision, recall, f1,
          conservatism_ratio.
    """
    import importlib.util as _ilu
    def _load(path, name):
        spec = _ilu.spec_from_file_location(name, path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    _sl_ss = _load(os.path.join(_REPO_ROOT, 'stateless', 'state_store.py'), 'sl_state_store')
    _sf_ss = _load(os.path.join(_REPO_ROOT, 'stateful', 'state_store.py'), 'sf_state_store')
    StateStore = _sl_ss.StateStore
    StatefulStateStore = _sf_ss.StatefulStateStore
    from fsm import ContainerFSM

    results    = []
    total_work = len(WINDOW_SECONDS_VALUES) * len(FAULT_SCENARIOS) * n
    pbar = tqdm(total=total_work, desc="Window sweep", unit="trial")

    for window in WINDOW_SECONDS_VALUES:
        for scenario in FAULT_SCENARIOS:
            sid   = scenario["id"]
            track = scenario["track"]
            trials = []

            for trial_idx in range(n):
                seed      = base_seed + trial_idx
                base_time = time.time() - 5.0 - trial_idx * 0.01
                events    = generate_events(scenario, seed=seed, jitter=True,
                                            base_time=base_time)
                n_cyc = len(scenario["expected_sequence"])
                if scenario["category"] == "cascading":
                    phase_plan = [(evs, True) for evs in events]
                elif n_cyc > 1:
                    phase_plan = [(events if idx == 0 else [], idx == 0)
                                  for idx in range(n_cyc)]
                else:
                    phase_plan = [(events, True)]

                all_decisions = []

                if track in ("S", "both"):
                    s_store = StateStore(window_seconds=window)
                    for phase_idx, (phase_events, inject) in enumerate(phase_plan):
                        if inject:
                            for ev in phase_events:
                                s_store.record(ev)
                        for container in s_store.get_all_containers():
                            d = _stateless_decide(
                                container, s_store,
                                restart_threshold=0.80,
                                reschedule_threshold=0.95,
                            )
                            d["phase"] = phase_idx
                            all_decisions.append(d)
                    ctrl_label = "stateless"

                else:  # F track
                    fsm     = ContainerFSM()
                    f_store = StatefulStateStore(window_seconds=window)
                    for phase_idx, (phase_events, inject) in enumerate(phase_plan):
                        if inject:
                            for ev in phase_events:
                                f_store.record(ev)
                        decs = _stateful_decide_all(
                            f_store, fsm,
                            flush_threshold=0.50,
                        )
                        for d in decs:
                            d["phase"] = phase_idx
                        all_decisions.extend(decs)
                    ctrl_label = "stateful"

                correct = _eval_correct(scenario, all_decisions)
                trials.append({
                    "scenario_id":     sid,
                    "expected_action": scenario["expected_action"],
                    "decisions":       all_decisions,
                    "correct":         correct,
                })
                pbar.update(1)

            metrics = _confusion_metrics(trials)
            conserv = round(
                sum(1 for t in trials
                    if _primary_action(t["decisions"]) == "no_action") / len(trials),
                4,
            ) if trials else 0.0
            results.append({
                "parameter":          "window_seconds",
                "value":              window,
                "controller":         ctrl_label,
                "fault_type":         sid,
                "precision":          metrics["precision"],
                "recall":             metrics["recall"],
                "f1":                 metrics["f1"],
                "conservatism_ratio": conserv,
            })

    pbar.close()
    return results


# ── Sweep 3: FSM flush threshold (stateful FLUSH_THRESHOLD) ───────────────────

def sweep_flush_threshold(n: int, base_seed: int) -> list:
    """
    Sweep the stateful FLUSH_THRESHOLD over [0.30, 0.40, 0.50, 0.60, 0.70].

    A lower flush threshold triggers flush_io_queue at lower risk scores,
    potentially increasing recall but also false positives. This sweep also
    records the unsafe action suppression rate to capture FSM gating behaviour.

    Args:
        n:         number of trials per threshold × scenario.
        base_seed: base RNG seed.

    Returns:
        List of result row dicts with fields:
          parameter, value, controller, fault_type, precision, recall, f1,
          unsafe_suppression_rate.
    """
    import importlib.util as _ilu2
    def _load2(path, name):
        spec = _ilu2.spec_from_file_location(name, path)
        mod = _ilu2.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    _sf_ss2 = _load2(os.path.join(_REPO_ROOT, 'stateful', 'state_store.py'), 'sf_state_store2')
    StatefulStateStore = _sf_ss2.StatefulStateStore
    from fsm import ContainerFSM

    f_scenarios = [s for s in FAULT_SCENARIOS if s["track"] in ("F", "both")]
    results     = []
    total_work  = len(FLUSH_THRESHOLD_VALUES) * len(f_scenarios) * n
    pbar = tqdm(total=total_work, desc="Flush threshold sweep", unit="trial")

    for threshold in FLUSH_THRESHOLD_VALUES:
        for scenario in f_scenarios:
            sid    = scenario["id"]
            trials = []

            for trial_idx in range(n):
                seed      = base_seed + trial_idx
                base_time = time.time() - 5.0 - trial_idx * 0.01
                events    = generate_events(scenario, seed=seed, jitter=True,
                                            base_time=base_time)
                n_cyc = len(scenario["expected_sequence"])
                if scenario["category"] == "cascading":
                    phase_plan = [(evs, True) for evs in events]
                elif n_cyc > 1:
                    phase_plan = [(events if idx == 0 else [], idx == 0)
                                  for idx in range(n_cyc)]
                else:
                    phase_plan = [(events, True)]

                all_decisions = []
                fsm     = ContainerFSM()
                f_store = StatefulStateStore(window_seconds=60)

                for phase_idx, (phase_events, inject) in enumerate(phase_plan):
                    if inject:
                        for ev in phase_events:
                            f_store.record(ev)
                    decs = _stateful_decide_all(
                        f_store, fsm,
                        flush_threshold=threshold,
                    )
                    for d in decs:
                        d["phase"] = phase_idx
                    all_decisions.extend(decs)

                correct = _eval_correct(scenario, all_decisions)
                trials.append({
                    "scenario_id":     sid,
                    "expected_action": scenario["expected_action"],
                    "decisions":       all_decisions,
                    "correct":         correct,
                })
                pbar.update(1)

            metrics = _confusion_metrics(trials)
            suppression = _unsafe_suppression_rate(trials)
            results.append({
                "parameter":               "flush_threshold",
                "value":                   threshold,
                "controller":              "stateful",
                "fault_type":              sid,
                "precision":               metrics["precision"],
                "recall":                  metrics["recall"],
                "f1":                      metrics["f1"],
                "unsafe_suppression_rate": suppression,
            })

    pbar.close()
    return results


# ── I/O ───────────────────────────────────────────────────────────────────────

def _write_csv(path: str, rows: list, fieldnames: list) -> None:
    """Write a list of dicts to a CSV file, creating parent directories as needed."""
    import csv
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    """Entry point for the sensitivity analysis CLI."""
    parser = argparse.ArgumentParser(
        description="UnderCurrent parameter sensitivity analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--n",      type=int, default=20,
                        help="Trials per parameter value × scenario (default: 20)")
    parser.add_argument("--seed",   type=int, default=42,
                        help="Base RNG seed (default: 42)")
    parser.add_argument("--output", type=str, default="evaluation/results",
                        help="Output root directory (default: evaluation/results)")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print("=== Sweep 1: RESTART_THRESHOLD (stateless) ===")
    results_threshold = sweep_restart_threshold(args.n, args.seed)

    print("\n=== Sweep 2: window_seconds (both tracks) ===")
    results_window = sweep_window_seconds(args.n, args.seed)

    print("\n=== Sweep 3: FLUSH_THRESHOLD (stateful) ===")
    results_flush = sweep_flush_threshold(args.n, args.seed)

    # Combine all results
    all_rows = []
    for r in results_threshold:
        row = {"parameter": r["parameter"], "value": r["value"],
               "controller": r["controller"], "fault_type": r["fault_type"],
               "precision": r["precision"], "recall": r["recall"], "f1": r["f1"]}
        all_rows.append(row)

    for r in results_window:
        row = {"parameter": r["parameter"], "value": r["value"],
               "controller": r["controller"], "fault_type": r["fault_type"],
               "precision": r["precision"], "recall": r["recall"], "f1": r["f1"]}
        all_rows.append(row)

    for r in results_flush:
        row = {"parameter": r["parameter"], "value": r["value"],
               "controller": r["controller"], "fault_type": r["fault_type"],
               "precision": r["precision"], "recall": r["recall"], "f1": r["f1"]}
        all_rows.append(row)

    out_path = os.path.join(args.output, "sensitivity_results.csv")
    _write_csv(
        out_path,
        all_rows,
        ["parameter", "value", "controller", "fault_type", "precision", "recall", "f1"],
    )
    print(f"\nWrote {len(all_rows)} rows to {out_path}")

    # Print compact summary per sweep
    def _print_sweep_summary(rows, sweep_name):
        print(f"\n{sweep_name} — macro-averaged F1 by parameter value:")
        by_val = defaultdict(list)
        for r in rows:
            by_val[r["value"]].append(r["f1"])
        for v in sorted(by_val):
            avg = round(statistics.mean(by_val[v]), 4) if by_val[v] else 0.0
            print(f"  {v:>8} → avg F1 = {avg:.4f}")

    _print_sweep_summary(results_threshold, "RESTART_THRESHOLD sweep")
    _print_sweep_summary(results_window,    "window_seconds sweep")
    _print_sweep_summary(results_flush,     "FLUSH_THRESHOLD sweep")


if __name__ == "__main__":
    main()
