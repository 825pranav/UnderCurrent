# evaluation/compute_metrics.py — UnderCurrent Evaluation Metric Aggregator
#
# Loads all trial JSON files produced by run_trials.py and computes:
#   1. Confusion matrices (TP/FP/FN/TN) with derived precision, recall, F1
#   2. Wilcoxon signed-rank tests (or sign-test fallback) for pairwise comparisons
#   3. Conservatism ratio for gray-zone scenarios
#   4. Decision latency statistics (mean, median, p95, p99)
#
# Also imports shared/metrics.py's compute_all() and appends those results.
#
# CLI usage:
#   python evaluation/compute_metrics.py [--results-dir evaluation/results]
#                                        [--output evaluation/results]

import sys
import os
import json
import csv
import time
import math
import statistics
import argparse
import glob as _glob

# ── sys.path: ensure sibling packages are importable ──────────────────────────
_REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_EVAL_DIR  = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateless'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'stateful'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'shared'))
sys.path.insert(0, _EVAL_DIR)

# ── Optional scipy ─────────────────────────────────────────────────────────────
try:
    from scipy.stats import wilcoxon as _scipy_wilcoxon
    _SCIPY_AVAILABLE = True
except Exception:
    _SCIPY_AVAILABLE = False


# ── Confusion matrix helpers ──────────────────────────────────────────────────

def _classify_decision(action: str, expected_action: str) -> str:
    """
    Map a single (action, expected_action) pair to a confusion matrix cell.

    Classification rules:
      TP: action != 'no_action' AND expected != 'no_action' (correctly acted)
      TN: action == 'no_action' AND expected == 'no_action' (correctly inactive)
      FP: action != 'no_action' AND expected == 'no_action' (spurious action)
      FN: action == 'no_action' AND expected != 'no_action' (missed fault)

    Args:
        action:          the action taken by the controller.
        expected_action: the ground-truth expected action from the scenario.

    Returns:
        One of 'TP', 'TN', 'FP', 'FN'.
    """
    acted    = action != "no_action"
    expected = expected_action != "no_action"
    if acted and expected:
        return "TP"
    if not acted and not expected:
        return "TN"
    if acted and not expected:
        return "FP"
    # not acted, expected → FN
    return "FN"


def compute_confusion_matrices(trials: list) -> dict:
    """
    Compute confusion matrices aggregated by (controller, fault_type).

    Each trial contributes one cell based on the primary action taken across
    all decisions in that trial versus the scenario's expected_action.

    A trial's primary action is determined by the highest-priority non-no_action
    decision (if any); if all decisions are no_action, the primary action is
    'no_action'.

    Args:
        trials: flat list of trial result dicts loaded from JSON files.

    Returns:
        Dict keyed by (controller, fault_type) → {"TP", "TN", "FP", "FN",
        "precision", "recall", "f1", "n_trials"}.
    """
    from collections import defaultdict
    matrices = defaultdict(lambda: {"TP": 0, "TN": 0, "FP": 0, "FN": 0})

    for trial in trials:
        ctrl     = trial.get("controller", "unknown")
        sid      = trial.get("scenario_id", "unknown")
        expected = trial.get("expected_action", "no_action")
        action   = _primary_action_from_trial(trial)
        cell     = _classify_decision(action, expected)
        matrices[(ctrl, sid)][cell] += 1

    # Compute derived metrics
    result = {}
    for (ctrl, sid), cm in matrices.items():
        tp, fp, fn, tn = cm["TP"], cm["FP"], cm["FN"], cm["TN"]
        precision  = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall     = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1         = (2 * precision * recall / (precision + recall)
                      if (precision + recall) > 0 else 0.0)
        result[(ctrl, sid)] = {
            "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "precision": round(precision, 4),
            "recall":    round(recall,    4),
            "f1":        round(f1,        4),
            "n_trials":  tp + fp + fn + tn,
        }

    return result


def _primary_action_from_trial(trial: dict) -> str:
    """
    Extract the highest-priority action from a trial's decisions list.

    Priority (highest first): escalate, reschedule, checkpoint_and_restart,
    restart, flush_io_queue, no_action.

    Args:
        trial: trial result dict.

    Returns:
        The highest-priority action string.
    """
    _PRIORITY = {
        "escalate":               6,
        "reschedule":             5,
        "checkpoint_and_restart": 4,
        "restart":                3,
        "flush_io_queue":         2,
        "no_action":              1,
    }
    best   = "no_action"
    best_p = 0
    for d in trial.get("decisions", []):
        a = d.get("action", "no_action")
        p = _PRIORITY.get(a, 0)
        if p > best_p:
            best   = a
            best_p = p
    return best


# ── Wilcoxon / sign-test helpers ──────────────────────────────────────────────

def _sign_test_fallback(x: list, y: list) -> tuple:
    """
    A simple sign test used when scipy is unavailable.

    Counts the number of differences with positive and negative sign,
    then uses a binomial approximation to compute a p-value.

    This is a conservative fallback; the Wilcoxon signed-rank test (used when
    scipy is available) is more powerful for continuous paired data.

    Args:
        x: list of numeric values for condition A.
        y: list of numeric values for condition B.

    Returns:
        Tuple (p_value, statistic) where p_value is approximate.
    """
    diffs = [xi - yi for xi, yi in zip(x, y) if xi != yi]
    if not diffs:
        return 1.0, 0.0
    n_pos = sum(1 for d in diffs if d > 0)
    n     = len(diffs)
    # Two-tailed binomial p-value approximation via normal approximation
    prop = n_pos / n
    z    = abs(prop - 0.5) / (0.5 / math.sqrt(n)) if n > 0 else 0.0
    # Two-tailed p from standard normal CDF approximation
    p    = 2.0 * (1.0 - _norm_cdf(z))
    return round(p, 6), round(float(n_pos), 4)


def _norm_cdf(z: float) -> float:
    """
    Standard normal CDF via the error function.

    Args:
        z: standard normal variate.

    Returns:
        Probability P(Z <= z).
    """
    import math
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _cohens_d(a: list, b: list) -> float:
    """
    Compute Cohen's d effect size between two samples.

    Formula: (mean_a - mean_b) / pooled_std

    A pooled standard deviation of zero implies identical distributions;
    returns 0.0 in that case.

    Args:
        a: first sample (list of numeric values).
        b: second sample (list of numeric values).

    Returns:
        Cohen's d as a float; may be negative.
    """
    if len(a) < 2 or len(b) < 2:
        return 0.0
    mean_a = statistics.mean(a)
    mean_b = statistics.mean(b)
    var_a  = statistics.variance(a)
    var_b  = statistics.variance(b)
    pooled = math.sqrt((var_a + var_b) / 2.0)
    return round((mean_a - mean_b) / pooled, 4) if pooled > 0 else 0.0


def compute_pairwise_tests(trials: list) -> list:
    """
    Perform pairwise statistical tests between controllers for correctness and latency.

    For each pair of controllers (A, B) sharing the same scenarios, runs:
      - Wilcoxon signed-rank test (or sign-test fallback) on per-trial correctness
      - Wilcoxon signed-rank test on per-trial decision latency

    Returns a list of test result dicts with p-value and Cohen's d effect size.

    Args:
        trials: flat list of trial result dicts.

    Returns:
        List of pairwise test dicts, each with fields:
          metric, controller_a, controller_b, p_value, effect_size,
          significant_05, significant_01.
    """
    from collections import defaultdict

    # Group trials by (controller, scenario_id)
    grouped = defaultdict(list)
    for t in trials:
        key = (t.get("controller"), t.get("scenario_id"))
        grouped[key].append(t)

    controllers = sorted({t.get("controller") for t in trials})
    scenarios   = sorted({t.get("scenario_id") for t in trials})

    results = []
    for i in range(len(controllers)):
        for j in range(i + 1, len(controllers)):
            ca, cb = controllers[i], controllers[j]

            # Collect paired samples per scenario (match by trial_idx)
            correct_a, correct_b = [], []
            latency_a, latency_b = [], []

            for sid in scenarios:
                ta = {t["trial_idx"]: t for t in grouped.get((ca, sid), [])}
                tb = {t["trial_idx"]: t for t in grouped.get((cb, sid), [])}
                common_idx = sorted(set(ta) & set(tb))
                for idx in common_idx:
                    correct_a.append(1.0 if ta[idx]["correct"] else 0.0)
                    correct_b.append(1.0 if tb[idx]["correct"] else 0.0)
                    latency_a.append(ta[idx]["decision_latency_ns"])
                    latency_b.append(tb[idx]["decision_latency_ns"])

            if len(correct_a) < 2:
                continue

            # Correctness test
            for metric, va, vb in [
                ("correctness", correct_a, correct_b),
                ("decision_latency_ns", latency_a, latency_b),
            ]:
                diffs = [a - b for a, b in zip(va, vb)]
                if all(d == 0 for d in diffs):
                    p_val = 1.0
                    stat  = 0.0
                elif _SCIPY_AVAILABLE:
                    try:
                        stat_w, p_val = _scipy_wilcoxon(diffs)
                        stat = float(stat_w)
                    except Exception:
                        p_val, stat = _sign_test_fallback(va, vb)
                else:
                    p_val, stat = _sign_test_fallback(va, vb)

                d = _cohens_d(va, vb)
                results.append({
                    "metric":         metric,
                    "controller_a":   ca,
                    "controller_b":   cb,
                    "p_value":        round(float(p_val), 6),
                    "statistic":      round(float(stat),  4),
                    "effect_size":    d,
                    "significant_05": bool(p_val < 0.05),
                    "significant_01": bool(p_val < 0.01),
                    "n_pairs":        len(va),
                    "scipy_used":     _SCIPY_AVAILABLE,
                })

    return results


# ── Conservatism ratio ─────────────────────────────────────────────────────────

_GRAY_ZONE_SCENARIOS = {"ambiguous_01", "cpu_flicker_01"}


def compute_conservatism_ratios(trials: list) -> dict:
    """
    Compute the conservatism ratio for gray-zone (ambiguous / intermittent) scenarios.

    Conservatism ratio = count(no_action decisions) / total_decisions
    for trials whose scenario_id is in the gray-zone set.

    A higher ratio indicates a more conservative controller that avoids
    acting on low-confidence signals — desirable to avoid over-remediation.

    Args:
        trials: flat list of trial result dicts.

    Returns:
        Dict keyed by controller → conservatism ratio float.
    """
    from collections import defaultdict
    counts = defaultdict(lambda: {"no_action": 0, "total": 0})

    for trial in trials:
        if trial.get("scenario_id") not in _GRAY_ZONE_SCENARIOS:
            continue
        ctrl = trial.get("controller", "unknown")
        action = _primary_action_from_trial(trial)
        counts[ctrl]["total"]    += 1
        if action == "no_action":
            counts[ctrl]["no_action"] += 1

    result = {}
    for ctrl, c in counts.items():
        result[ctrl] = round(c["no_action"] / c["total"], 4) if c["total"] > 0 else 0.0
    return result


# ── Latency statistics ─────────────────────────────────────────────────────────

def compute_latency_stats(trials: list) -> dict:
    """
    Compute decision latency statistics per controller.

    Reports mean, median, p95 (95th percentile), and p99 (99th percentile)
    of the decision_latency_ns field across all trials for each controller.

    Args:
        trials: flat list of trial result dicts.

    Returns:
        Dict keyed by controller → {"mean_ns", "median_ns", "p95_ns", "p99_ns",
        "n_trials"}.
    """
    from collections import defaultdict
    lats = defaultdict(list)
    for t in trials:
        ctrl = t.get("controller", "unknown")
        lats[ctrl].append(t.get("decision_latency_ns", 0))

    result = {}
    for ctrl, values in lats.items():
        values.sort()
        n = len(values)
        result[ctrl] = {
            "mean_ns":   round(statistics.mean(values), 1)   if n > 0 else 0.0,
            "median_ns": round(statistics.median(values), 1) if n > 0 else 0.0,
            "p95_ns":    round(values[int(0.95 * n)], 1)    if n > 0 else 0.0,
            "p99_ns":    round(values[int(0.99 * n)], 1)    if n > 0 else 0.0,
            "n_trials":  n,
        }
    return result


# ── I/O helpers ───────────────────────────────────────────────────────────────

def load_all_trials(results_dir: str) -> list:
    """
    Recursively load all trial JSON files from the results directory tree.

    Expects the directory layout produced by run_trials.py:
      {results_dir}/{controller}/{scenario_id}/trial_*.json

    Args:
        results_dir: root directory path as a string.

    Returns:
        Flat list of trial result dicts, sorted by (controller, scenario_id, trial_idx).
    """
    pattern = os.path.join(results_dir, "*", "*", "trial_*.json")
    paths   = sorted(_glob.glob(pattern))
    trials  = []
    for path in paths:
        try:
            with open(path) as fh:
                trials.append(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  Warning: could not load {path}: {exc}", file=sys.stderr)
    return trials


def _write_csv(path: str, rows: list, fieldnames: list) -> None:
    """
    Write a list of dicts to a CSV file.

    Args:
        path:       destination file path.
        rows:       list of dicts to write.
        fieldnames: ordered list of column names.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Main computation pipeline ─────────────────────────────────────────────────

def run_metrics(results_dir: str, output_dir: str) -> dict:
    """
    Full metric computation pipeline: load trials, compute all metrics, write CSVs.

    Runs the following computations:
      1. Load all trial JSON files.
      2. Compute confusion matrices → summary_table.csv, confusion_matrices.csv.
      3. Compute pairwise statistical tests → pairwise_tests.csv.
      4. Compute decision latency stats → latency_report.csv.
      5. Write per_trial_raw.csv.
      6. Call shared/metrics.py compute_all() and append to summary.
      7. Print a summary to stdout.

    Args:
        results_dir: directory containing trial JSON files.
        output_dir:  directory where metric CSV files will be written.

    Returns:
        Dict with keys: confusion_matrices, pairwise_tests, latency_stats,
        conservatism_ratios, shared_metrics.
    """
    os.makedirs(output_dir, exist_ok=True)

    print(f"Loading trials from {results_dir} …")
    trials = load_all_trials(results_dir)
    print(f"  Loaded {len(trials)} trials.")

    if not trials:
        print("No trials found. Run run_trials.py first.")
        return {}

    # ── 1. Confusion matrices ──────────────────────────────────────────────────
    print("Computing confusion matrices …")
    cm = compute_confusion_matrices(trials)

    summary_rows = []
    cm_rows      = []
    for (ctrl, sid), data in sorted(cm.items()):
        summary_rows.append({
            "controller": ctrl,
            "fault_type": sid,
            "precision":  data["precision"],
            "recall":     data["recall"],
            "f1":         data["f1"],
            "n_trials":   data["n_trials"],
            "mean_latency_ns": 0,  # filled in below after latency stats
        })
        cm_rows.append({
            "controller": ctrl,
            "fault_type": sid,
            "tp": data["TP"],
            "fp": data["FP"],
            "fn": data["FN"],
            "tn": data["TN"],
        })

    # ── 2. Latency stats ───────────────────────────────────────────────────────
    print("Computing latency statistics …")
    lat_stats  = compute_latency_stats(trials)
    lat_lookup = {ctrl: d["mean_ns"] for ctrl, d in lat_stats.items()}

    # Patch mean_latency_ns into summary
    for row in summary_rows:
        row["mean_latency_ns"] = round(lat_lookup.get(row["controller"], 0), 1)

    # ── 3. Pairwise tests ──────────────────────────────────────────────────────
    print("Running pairwise statistical tests …")
    pairwise = compute_pairwise_tests(trials)

    # ── 4. Conservatism ratios ─────────────────────────────────────────────────
    conservatism = compute_conservatism_ratios(trials)
    print(f"Conservatism ratios (gray-zone scenarios): {conservatism}")

    # ── 5. Write CSVs ──────────────────────────────────────────────────────────
    _write_csv(
        os.path.join(output_dir, "summary_table.csv"),
        summary_rows,
        ["controller", "fault_type", "precision", "recall", "f1",
         "mean_latency_ns", "n_trials"],
    )
    _write_csv(
        os.path.join(output_dir, "confusion_matrices.csv"),
        cm_rows,
        ["controller", "fault_type", "tp", "fp", "fn", "tn"],
    )
    _write_csv(
        os.path.join(output_dir, "pairwise_tests.csv"),
        pairwise,
        ["metric", "controller_a", "controller_b", "p_value", "effect_size",
         "significant_05", "significant_01", "n_pairs", "scipy_used"],
    )

    lat_rows = [
        {"controller": ctrl, **data}
        for ctrl, data in sorted(lat_stats.items())
    ]
    _write_csv(
        os.path.join(output_dir, "latency_report.csv"),
        lat_rows,
        ["controller", "mean_ns", "median_ns", "p95_ns", "p99_ns", "n_trials"],
    )

    # ── 6. Per-trial raw CSV ───────────────────────────────────────────────────
    raw_rows = []
    for t in trials:
        raw_rows.append({
            "scenario_id":          t.get("scenario_id"),
            "controller":           t.get("controller"),
            "trial_idx":            t.get("trial_idx"),
            "seed":                 t.get("seed"),
            "correct":              int(t.get("correct", 0)),
            "decision_latency_ns":  t.get("decision_latency_ns", 0),
            "injected_event_count": t.get("injected_event_count", 0),
            "expected_action":      t.get("expected_action"),
            "primary_action":       _primary_action_from_trial(t),
            "timestamp":            t.get("timestamp", 0),
        })
    _write_csv(
        os.path.join(output_dir, "per_trial_raw.csv"),
        raw_rows,
        ["scenario_id", "controller", "trial_idx", "seed", "correct",
         "decision_latency_ns", "injected_event_count", "expected_action",
         "primary_action", "timestamp"],
    )

    # ── 7. shared/metrics.py compute_all() ────────────────────────────────────
    print("Running shared/metrics.py compute_all() …")
    shared_metrics = {}
    try:
        from metrics import compute_all as _compute_all
        sl_trace = os.path.join(_REPO_ROOT, "stateless", "traces.jsonl")
        sf_trace = os.path.join(_REPO_ROOT, "stateful",  "traces.jsonl")
        sl_div   = os.path.join(_REPO_ROOT, "stateless", "divergence_log.jsonl")
        sf_div   = os.path.join(_REPO_ROOT, "stateful",  "divergence_log.jsonl")
        shared_metrics = _compute_all(sl_trace, sf_trace, sl_div, sf_div)
        # Append shared metric summary rows to summary_table.csv
        sm_rows = []
        for metric_name, value in shared_metrics.items():
            if isinstance(value, dict):
                for sub, v in value.items():
                    sm_rows.append({
                        "controller": f"shared/{sub}",
                        "fault_type": metric_name,
                        "precision":  "",
                        "recall":     "",
                        "f1":         v if isinstance(v, float) else "",
                        "mean_latency_ns": "",
                        "n_trials":   "",
                    })
            else:
                sm_rows.append({
                    "controller": "shared",
                    "fault_type": metric_name,
                    "precision":  "",
                    "recall":     "",
                    "f1":         value if isinstance(value, float) else "",
                    "mean_latency_ns": "",
                    "n_trials":   "",
                })
        if sm_rows:
            path = os.path.join(output_dir, "summary_table.csv")
            with open(path, "a", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["controller", "fault_type", "precision", "recall",
                                "f1", "mean_latency_ns", "n_trials"],
                    extrasaction="ignore",
                )
                writer.writerows(sm_rows)
        print(f"  Shared metrics: {list(shared_metrics)}")
    except Exception as exc:
        print(f"  Warning: shared/metrics.py compute_all() failed: {exc}",
              file=sys.stderr)

    # ── Print summary ──────────────────────────────────────────────────────────
    print("\n=== Metric Summary ===")
    col = [16, 32, 8, 8, 8, 14]
    print(f"{'Controller':<{col[0]}} {'Scenario':<{col[1]}} "
          f"{'F1':>{col[2]}} {'Prec':>{col[3]}} {'Rec':>{col[4]}} "
          f"{'MeanLat(µs)':>{col[5]}}")
    print("-" * (sum(col) + len(col)))
    for row in sorted(summary_rows, key=lambda r: (r["controller"], r["fault_type"])):
        mean_us = round(row["mean_latency_ns"] / 1000, 1)
        print(f"{row['controller']:<{col[0]}} {row['fault_type']:<{col[1]}} "
              f"{row['f1']:>{col[2]}.3f} {row['precision']:>{col[3]}.3f} "
              f"{row['recall']:>{col[4]}.3f} {mean_us:>{col[5]}.1f}")

    print(f"\nConservatism ratios: {conservatism}")

    return {
        "confusion_matrices": cm,
        "pairwise_tests":     pairwise,
        "latency_stats":      lat_stats,
        "conservatism_ratios": conservatism,
        "shared_metrics":     shared_metrics,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    """Entry point for the compute_metrics CLI."""
    parser = argparse.ArgumentParser(
        description="UnderCurrent evaluation metric aggregator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--results-dir", type=str, default="evaluation/results",
                        help="Directory containing trial JSON files (default: evaluation/results)")
    parser.add_argument("--output",      type=str, default="evaluation/results",
                        help="Directory for output CSV files (default: evaluation/results)")
    args = parser.parse_args()

    # Resolve paths relative to repo root when run from repo root
    results_dir = args.results_dir
    output_dir  = args.output

    run_metrics(results_dir, output_dir)
    print(f"\nOutput files written to {output_dir}/")


if __name__ == "__main__":
    main()
