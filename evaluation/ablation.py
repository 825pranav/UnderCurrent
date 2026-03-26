# evaluation/ablation.py — UnderCurrent Stateful Controller Ablation Study
#
# Creates four ablation variants of the stateful controller and runs each
# through the trial harness to quantify the contribution of individual
# components (FSM gating, confidence scoring, window size) to overall
# controller performance.
#
# Variants:
#   stateful_no_fsm        — stateful confidence scoring, no FSM gating
#   stateful_no_confidence — FSM preserved, raw event-count score
#   stateful_short_window  — half window (30s)
#   stateful_long_window   — double window (120s)
#
# CLI usage:
#   python evaluation/ablation.py [--n 20] [--seed 42] [--output evaluation/results]

import sys
import os
import json
import time
import math
import statistics
import argparse
from collections import defaultdict

# ── sys.path: ensure sibling packages are importable ──────────────────────────
_REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_EVAL_DIR  = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateless'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateful'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'shared'))
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


# ── Ablation variant definitions ──────────────────────────────────────────────

ABLATION_VARIANTS = [
    {
        "name":        "stateful_no_fsm",
        "description": (
            "Stateful confidence scoring (blk_io, vfs, volume_mount_lost) "
            "but all FSM gating is bypassed. Actions are chosen purely from "
            "raw confidence thresholds, equivalent to stateless decision logic "
            "applied to stateful signals. Isolates the contribution of FSM gating."
        ),
        "window_seconds": 60,
        "use_fsm":        False,
        "use_confidence": True,
    },
    {
        "name":        "stateful_no_confidence",
        "description": (
            "FSM is preserved but the confidence score is replaced by a "
            "raw event-count score: score = min(event_count / 10, 1.0). "
            "Isolates the contribution of the stateful confidence scorer."
        ),
        "window_seconds": 60,
        "use_fsm":        True,
        "use_confidence": False,
    },
    {
        "name":        "stateful_short_window",
        "description": (
            "Full stateful controller (FSM + confidence) with the sliding "
            "window halved to 30 seconds. Tests sensitivity to window size: "
            "shorter windows may miss slow-onset faults or lose context."
        ),
        "window_seconds": 30,
        "use_fsm":        True,
        "use_confidence": True,
    },
    {
        "name":        "stateful_long_window",
        "description": (
            "Full stateful controller (FSM + confidence) with the sliding "
            "window doubled to 120 seconds. Tests whether a longer memory "
            "improves detection of intermittent faults at the cost of slower "
            "recovery from stale signals."
        ),
        "window_seconds": 120,
        "use_fsm":        True,
        "use_confidence": True,
    },
]


# ── Ablation decision engine ───────────────────────────────────────────────────

def _run_ablation_decision(variant: dict, f_store, fsm,
                            container: str) -> dict:
    """
    Produce a decision for one container under the given ablation variant.

    Dispatches to:
      - Full stateful reconcile  (use_fsm=True,  use_confidence=True)
      - No-FSM path              (use_fsm=False, use_confidence=True)
      - Raw-count scoring path   (use_fsm=True,  use_confidence=False)

    Args:
        variant:   ablation variant config dict.
        f_store:   StatefulStateStore with current events.
        fsm:       ContainerFSM instance (may be mutated if use_fsm=True).
        container: name of the container to evaluate.

    Returns:
        Decision dict compatible with the standard reconcile dict format.
    """
    from state_store import StatefulStateStore
    from fsm import ContainerFSM
    import confidence as sf_conf

    FLUSH_THRESHOLD    = 0.50
    REPAIR_THRESHOLD   = 0.80
    ESCALATE_THRESHOLD = 0.95
    DEGRADE_THRESHOLD  = 0.50

    failures = f_store.get_failures(container)

    # ── Compute score ──────────────────────────────────────────────────────────
    if variant["use_confidence"]:
        score = sf_conf.compute_confidence(container, f_store)
    else:
        # Raw event count score
        count = len(failures)
        score = round(min(count / 10.0, 1.0), 4)

    # ── Raw DAG decision (no FSM) ──────────────────────────────────────────────
    if score >= ESCALATE_THRESHOLD:
        raw_action  = "escalate"
        dag_pattern = "io_degradation_critical"
        reason      = f"score {score} >= escalate threshold {ESCALATE_THRESHOLD}"
    elif score >= REPAIR_THRESHOLD:
        raw_action  = "checkpoint_and_restart"
        dag_pattern = "io_degradation_major"
        reason      = f"score {score} >= repair threshold {REPAIR_THRESHOLD}"
    elif score >= FLUSH_THRESHOLD:
        raw_action  = "flush_io_queue"
        dag_pattern = "io_degradation_minor"
        reason      = f"score {score} >= flush threshold {FLUSH_THRESHOLD}"
    else:
        raw_action  = "no_action"
        dag_pattern = "nominal"
        reason      = f"score {score} below all thresholds"

    action = raw_action

    # ── FSM gating (only when use_fsm=True) ───────────────────────────────────
    fsm_state_before = fsm.state(container) if variant["use_fsm"] else "N/A"
    fsm_transition   = None
    blocked_reason   = None

    if variant["use_fsm"] and action != "no_action":
        current = fsm.state(container)
        if score >= DEGRADE_THRESHOLD and current in ("Healthy", "Recovered"):
            t = fsm.apply(container, "degrade")
            if t:
                fsm_transition = f"{t[0]}→{t[1]}"
            current = fsm.state(container)

        if action == "checkpoint_and_restart" and current != "Audited":
            action         = "flush_io_queue"
            blocked_reason = (
                f"checkpoint_and_restart deferred: FSM is {current}, "
                f"must reach Audited first"
            )
            reason = blocked_reason

        if action == "flush_io_queue" and current == "Degraded":
            t = fsm.apply(container, "audit")
            if t:
                fsm_transition = f"{t[0]}→{t[1]}"
                current = fsm.state(container)

        if action == "checkpoint_and_restart" and current == "Audited":
            t = fsm.apply(container, "approve_repair")
            if t:
                fsm_transition = f"{t[0]}→{t[1]}"
                current = fsm.state(container)

        if action == "escalate" and current not in ("Degraded", "Audited", "Repairing"):
            action         = "flush_io_queue"
            blocked_reason = (
                f"escalate blocked: FSM is {current}; need Degraded/Audited/Repairing"
            )
            reason = blocked_reason

    fsm_state_after = fsm.state(container) if variant["use_fsm"] else "N/A"

    return {
        "container":       container,
        "score":           score,
        "action":          action,
        "reason":          reason,
        "mode":            "real",
        "dag_pattern":     dag_pattern,
        "kernel_signals":  [],
        "fsm_state":       fsm_state_before,
        "fsm_state_after": fsm_state_after,
        "fsm_transition":  fsm_transition,
        "blocked_reason":  blocked_reason,
        "timestamp":       time.time(),
        "variant":         variant["name"],
    }


def run_ablation_trial(variant: dict, scenario: dict,
                       trial_idx: int, seed: int) -> dict:
    """
    Execute one complete ablation trial for a variant × scenario combination.

    Creates a fresh StatefulStateStore with the variant's window_seconds,
    injects events, runs the ablation decision engine, and records results.

    Args:
        variant:   ablation variant config dict.
        scenario:  scenario dict from FAULT_SCENARIOS (F-track only).
        trial_idx: zero-based trial index.
        seed:      RNG seed for event generation.

    Returns:
        Trial result dict with the same structure as run_trials.py produces,
        plus a 'variant' field.
    """
    from state_store import StatefulStateStore
    from fsm import ContainerFSM

    base_time = time.time() - 5.0 - trial_idx * 0.01
    category  = scenario["category"]
    raw_events = generate_events(scenario, seed=seed, jitter=True,
                                 base_time=base_time)

    n_cycles = len(scenario.get("expected_sequence", []))
    if category == "cascading":
        phases = [(evs, True) for evs in raw_events]
        injected_event_count = sum(len(p) for p in raw_events)
    elif n_cycles > 1:
        phases = [(raw_events if idx == 0 else [], idx == 0)
                  for idx in range(n_cycles)]
        injected_event_count = len(raw_events)
    else:
        phases = [(raw_events, True)]
        injected_event_count = len(raw_events)

    all_decisions    = []
    total_latency_ns = 0

    f_store = StatefulStateStore(window_seconds=variant["window_seconds"])
    fsm     = ContainerFSM()

    for phase_idx, (phase_events, inject) in enumerate(phases):
        if inject:
            for ev in phase_events:
                f_store.record(ev)

        t0 = time.perf_counter_ns()
        phase_decisions = []
        for container in f_store.get_all_containers():
            d = _run_ablation_decision(variant, f_store, fsm, container)
            d["phase"] = phase_idx
            phase_decisions.append(d)
        t1 = time.perf_counter_ns()
        total_latency_ns += (t1 - t0)
        all_decisions.extend(phase_decisions)

    correct = _eval_ablation_correctness(scenario, all_decisions)

    return {
        "scenario_id":          scenario["id"],
        "controller":           variant["name"],
        "variant":              variant["name"],
        "trial_idx":            trial_idx,
        "seed":                 seed,
        "decisions":            all_decisions,
        "expected_action":      scenario["expected_action"],
        "expected_sequence":    scenario["expected_sequence"],
        "correct":              correct,
        "decision_latency_ns":  total_latency_ns,
        "injected_event_count": injected_event_count,
        "timestamp":            time.time(),
        "window_seconds":       variant["window_seconds"],
    }


def _eval_ablation_correctness(scenario: dict, decisions: list) -> bool:
    """
    Evaluate correctness for an ablation trial (same logic as run_trials.py).

    Args:
        scenario:  scenario dict.
        decisions: list of decision dicts from the trial.

    Returns:
        True if the decisions match the expected outcome.
    """
    _PRIORITY = {
        "escalate": 6, "reschedule": 5, "checkpoint_and_restart": 4,
        "restart": 3, "flush_io_queue": 2, "no_action": 1,
    }

    def _primary(decs):
        best, best_p = "no_action", 0
        for d in decs:
            a = d.get("action", "no_action")
            p = _PRIORITY.get(a, 0)
            if p > best_p:
                best, best_p = a, p
        return best

    category          = scenario["category"]
    expected_action   = scenario["expected_action"]
    expected_sequence = scenario["expected_sequence"]

    n_cycles = len(expected_sequence)
    if category == "cascading" or n_cycles > 1:
        max_phase = max((d.get("phase", 0) for d in decisions), default=0)
        phase_actions = []
        for p in range(max_phase + 1):
            phase_decs = [d for d in decisions if d.get("phase") == p]
            phase_actions.append(_primary(phase_decs))
        return phase_actions == expected_sequence
    else:
        primary = _primary(decisions)
        if expected_action == "no_action":
            return primary == "no_action"
        return primary == expected_action


# ── Conservatism ratio ─────────────────────────────────────────────────────────

_GRAY_ZONE = {"ambiguous_01", "cpu_flicker_01"}


def _conservatism_ratio(results: list, variant_name: str) -> float:
    """
    Compute conservatism ratio (no_action rate on gray-zone scenarios) for a variant.

    Args:
        results:      list of trial result dicts for this variant.
        variant_name: variant name string for filtering.

    Returns:
        Conservatism ratio float in [0.0, 1.0].
    """
    _PRIORITY = {
        "escalate": 6, "reschedule": 5, "checkpoint_and_restart": 4,
        "restart": 3, "flush_io_queue": 2, "no_action": 1,
    }

    def _primary(decs):
        best, best_p = "no_action", 0
        for d in decs:
            a = d.get("action", "no_action")
            p = _PRIORITY.get(a, 0)
            if p > best_p:
                best, best_p = a, p
        return best

    gray = [r for r in results if r.get("scenario_id") in _GRAY_ZONE]
    if not gray:
        return 0.0
    no_act = sum(1 for r in gray if _primary(r.get("decisions", [])) == "no_action")
    return round(no_act / len(gray), 4)


# ── MTTR from ablation trials ─────────────────────────────────────────────────

def _compute_mttr(results: list) -> float:
    """
    Estimate MTTR from ablation trial results.

    For each trial with expected_action != 'no_action', the MTTR is estimated
    as a proxy: if correct, MTTR ≈ decision_latency_ns / 1e9; if incorrect
    (fault not caught), MTTR is treated as undefined (excluded).

    This is a proxy metric; true MTTR requires timed system logs.

    Args:
        results: list of trial result dicts.

    Returns:
        Mean proxy MTTR in seconds, or 0.0 if no valid samples.
    """
    samples = []
    for r in results:
        if r.get("expected_action") == "no_action":
            continue
        if r.get("correct"):
            # Proxy: decision latency as lower bound on MTTR
            lat_s = r.get("decision_latency_ns", 0) / 1e9
            samples.append(lat_s)
    if not samples:
        return 0.0
    return round(statistics.mean(samples), 6)


# ── Comparison table printer ──────────────────────────────────────────────────

def print_comparison_table(all_results: dict) -> None:
    """
    Print a formatted comparison table of variant × metric.

    Shows F1 score, proxy MTTR, and conservatism ratio for each variant.

    Args:
        all_results: dict mapping variant_name → list of trial result dicts.
    """
    from compute_metrics import compute_confusion_matrices

    print("\n=== Ablation Comparison Table ===")
    col = [28, 8, 10, 14]
    hdr = (f"{'Variant':<{col[0]}} {'F1':>{col[1]}} "
           f"{'MTTR(s)':>{col[2]}} {'Conservatism':>{col[3]}}")
    sep = "-" * (sum(col) + len(col))
    print(hdr)
    print(sep)

    for variant_name, results in sorted(all_results.items()):
        cm = compute_confusion_matrices(results)
        # Macro-average F1 across scenarios
        f1_vals = [data["f1"] for data in cm.values()]
        avg_f1  = round(statistics.mean(f1_vals), 4) if f1_vals else 0.0
        mttr    = _compute_mttr(results)
        conserv = _conservatism_ratio(results, variant_name)
        print(f"{variant_name:<{col[0]}} {avg_f1:>{col[1]}.4f} "
              f"{mttr:>{col[2]}.6f} {conserv:>{col[3]}.4f}")

    print(sep)


# ── Main runner ───────────────────────────────────────────────────────────────

def run_ablation(n: int, base_seed: int, output_dir: str) -> None:
    """
    Execute the full ablation study: run all variants × all F-track scenarios × N trials.

    Saves trial results to:
      {output_dir}/ablation/{variant_name}/{scenario_id}/trial_{idx:03d}.json

    Prints a comparison table when complete.

    Args:
        n:          number of trials per variant × scenario combination.
        base_seed:  base RNG seed.
        output_dir: root directory for output files.
    """
    # Use only F-track scenarios for ablation (ablation is over stateful controller)
    f_scenarios = [s for s in FAULT_SCENARIOS if s["track"] in ("F", "both")]
    print(f"Ablation study: {len(ABLATION_VARIANTS)} variants × "
          f"{len(f_scenarios)} F-track scenarios × {n} trials")

    all_results = {}
    total_work  = len(ABLATION_VARIANTS) * len(f_scenarios) * n
    pbar = tqdm(total=total_work, desc="Ablation trials", unit="trial")

    for variant in ABLATION_VARIANTS:
        vname        = variant["name"]
        var_results  = []
        all_results[vname] = var_results

        for scenario in f_scenarios:
            sid     = scenario["id"]
            out_dir = os.path.join(output_dir, "ablation", vname, sid)
            os.makedirs(out_dir, exist_ok=True)

            for trial_idx in range(n):
                seed   = base_seed + trial_idx
                result = run_ablation_trial(variant, scenario, trial_idx, seed)

                out_path = os.path.join(out_dir, f"trial_{trial_idx:03d}.json")
                with open(out_path, "w") as fh:
                    json.dump(result, fh, indent=2, default=str)

                var_results.append(result)
                pbar.update(1)

    pbar.close()
    print_comparison_table(all_results)
    print(f"\nAblation results written to {output_dir}/ablation/")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    """Entry point for the ablation CLI."""
    parser = argparse.ArgumentParser(
        description="UnderCurrent stateful controller ablation study",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--n",      type=int, default=20,
                        help="Trials per variant × scenario (default: 20)")
    parser.add_argument("--seed",   type=int, default=42,
                        help="Base RNG seed (default: 42)")
    parser.add_argument("--output", type=str, default="evaluation/results",
                        help="Output root directory (default: evaluation/results)")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    run_ablation(n=args.n, base_seed=args.seed, output_dir=args.output)


if __name__ == "__main__":
    main()
