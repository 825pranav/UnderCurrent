# shared/real_metrics_report.py — UnderCurrent Real-Data Metrics Report
#
# Computes all 6 research metrics from the actual trace files produced by the
# running system. Unlike eval_runner.py (which uses trace_time and contains
# hard-coded simulation assumptions), this script:
#
#   - Filters mode="real" entries before ALL metric computations
#   - Uses decision_timestamp for M3 MTTR (when the decision was made, not
#     when the log entry was written after action execution)
#   - Audits each trace file for schema gaps before computing
#   - Reports per-container MTTR breakdown
#   - Generates caveats dynamically from what is actually in the data
#   - Saves output to shared/real_metrics_output.json
#
# Usage (from repo root):
#   python3 shared/real_metrics_report.py
#   python3 shared/real_metrics_report.py --out path/to/output.json
#
# Requires:
#   stateless/traces.jsonl  — produced by running stateless/main.py
#   stateful/traces.jsonl   — produced by running stateful/main.py
#
# Optional (divergence rate is 0.0 if absent):
#   stateful/divergence_log.jsonl

import argparse
import collections
import json
import os
import sys
import time

# ── Make shared/ importable regardless of working directory ──────────────────
_here      = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_here)
if _here not in sys.path:
    sys.path.insert(0, _here)

from metrics import (
    _load_jsonl,
    _real,
    _by_node_type,
    misclassification_rate,
    divergence_rate_from_traces,
    reversibility_ratio,
    explanation_completeness,
    unsafe_action_suppression_rate,
)

# ── Canonical file paths ──────────────────────────────────────────────────────
_PATHS = {
    "stateless_traces":  os.path.join(_repo_root, "stateless", "traces.jsonl"),
    "stateful_traces":   os.path.join(_repo_root, "stateful",  "traces.jsonl"),
    "stateful_div_log":  os.path.join(_repo_root, "stateful",  "divergence_log.jsonl"),
    "stateless_div_log": os.path.join(_repo_root, "stateless", "divergence_log.jsonl"),
}

_DEFAULT_OUT = os.path.join(_here, "real_metrics_output.json")


# ── M3: MTTR using decision_timestamp ────────────────────────────────────────

def mttr_real(traces: list, node_type: str = None) -> dict:
    """
    Compute MTTR from real-mode traces using decision_timestamp.

    decision_timestamp is set by reconcile.py at decision time, before action
    execution. It is more accurate than trace_time (set at log-write time,
    after execute() returns) because docker restart / checkpoint operations
    can take several seconds, inflating trace_time-based MTTR measurements.

    Algorithm per container:
      1. Filter to real-mode traces; optionally filter by node_type.
      2. Skip entries where decision_timestamp is null or zero.
      3. Sort each container's events by decision_timestamp.
      4. Fault onset  = decision_timestamp of first non-no_action entry.
      5. Recovery     = decision_timestamp of first subsequent no_action entry.
      6. MTTR sample  = recovery_ts - onset_ts  (seconds).
      7. Report mean and episode count; include per-container breakdown.

    Returns:
        {
          "mean_s":               float,   # mean MTTR across all episodes
          "n_episodes":           int,     # number of complete fault→recovery pairs
          "per_container":        {str: [float, ...]},  # per-container sample list
          "null_timestamp_count": int,     # entries skipped due to null decision_ts
        }
    """
    candidates = _by_node_type(_real(traces), node_type)

    by_container: dict = collections.defaultdict(list)
    null_ts_count = 0
    for t in candidates:
        ts = t.get("decision_timestamp")
        if not ts:
            null_ts_count += 1
            continue
        container = t.get("container")
        if container:
            by_container[container].append(t)

    per_container: dict = {}
    all_samples: list   = []

    for container, events in by_container.items():
        events_sorted = sorted(events, key=lambda x: float(x.get("decision_timestamp", 0)))
        per_container[container] = []
        onset_ts = None
        for ev in events_sorted:
            action = ev.get("action", "no_action")
            ts     = float(ev.get("decision_timestamp", 0))
            if onset_ts is None and action != "no_action":
                onset_ts = ts
            elif onset_ts is not None and action == "no_action":
                sample = round(ts - onset_ts, 4)
                per_container[container].append(sample)
                all_samples.append(sample)
                onset_ts = None   # reset — detect subsequent fault episodes

    mean_s = round(sum(all_samples) / len(all_samples), 4) if all_samples else 0.0
    return {
        "mean_s":               mean_s,
        "n_episodes":           len(all_samples),
        "per_container":        per_container,
        "null_timestamp_count": null_ts_count,
    }


# ── Data-quality audit ────────────────────────────────────────────────────────

def _audit_track(traces: list) -> dict:
    """Count schema gaps in a list of trace entries."""
    real   = [t for t in traces if t.get("mode") == "real"]
    shadow = [t for t in traces if t.get("mode") == "shadow"]
    return {
        "total":                     len(traces),
        "real":                      len(real),
        "shadow":                    len(shadow),
        "missing_node_type":         sum(1 for t in traces if not t.get("node_type")),
        "missing_decision_timestamp":sum(1 for t in real   if not t.get("decision_timestamp")),
        "missing_dag_pattern":       sum(1 for t in real   if not t.get("dag_pattern")),
        "missing_kernel_signals":    sum(1 for t in real   if t.get("kernel_signals") is None),
        "missing_reversibility":     sum(
            1 for t in real
            if t.get("action") != "no_action" and t.get("reversibility") is None
        ),
    }


# ── Main report ───────────────────────────────────────────────────────────────

def run(out_path: str = _DEFAULT_OUT) -> dict:
    """
    Load trace files, compute all 6 metrics on real data, write output.

    Returns the full results dict.
    """
    # ── 1. Verify trace files exist ───────────────────────────────────────────
    required = ["stateless_traces", "stateful_traces"]
    missing  = [name for name in required if not os.path.exists(_PATHS[name])]
    if missing:
        print("[real_metrics_report] ERROR — trace files not found:")
        for name in missing:
            print(f"  {name}: {_PATHS[name]}")
        print()
        print("Run the system first to generate trace logs:")
        print("  python3 run.py          # both tracks + dashboard")
        print("  # OR independently:")
        print("  cd stateless && python3 main.py")
        print("  cd stateful  && python3 main.py")
        sys.exit(1)

    # ── 2. Load ───────────────────────────────────────────────────────────────
    sl_all = _load_jsonl(_PATHS["stateless_traces"])
    sf_all = _load_jsonl(_PATHS["stateful_traces"])

    sl_real = [t for t in sl_all if t.get("mode") == "real"]
    sf_real = [t for t in sf_all if t.get("mode") == "real"]
    all_real = sl_real + sf_real

    # ── 3. Data-quality audit ─────────────────────────────────────────────────
    quality = {
        "stateless": _audit_track(sl_all),
        "stateful":  _audit_track(sf_all),
    }

    # ── 4. M1: Misclassification rate ─────────────────────────────────────────
    # Uses all traces (both modes) from each file.  Source-file separation
    # (sl_all = S track, sf_all = F track) is the correct input split.
    m1 = misclassification_rate(sl_all, sf_all)

    # ── 5. M2: Divergence rate ────────────────────────────────────────────────
    # Denominator = real-mode entries per track; numerator = divergence log entries.
    # stateless/divergence_log.jsonl typically does not exist (stateless track does
    # not diverge: real and shadow use the same pure-threshold DAG with no FSM state).
    m2 = divergence_rate_from_traces(
        sl_all, sf_all,
        _PATHS["stateless_div_log"],
        _PATHS["stateful_div_log"],
    )

    # ── 6. M3: MTTR using decision_timestamp ─────────────────────────────────
    m3_stateless = mttr_real(all_real, node_type="S")
    m3_stateful  = mttr_real(all_real, node_type="F")
    m3_all       = mttr_real(all_real)

    # ── 7. M4: Reversibility ratio ────────────────────────────────────────────
    # Real-mode only.  Both stateless and stateful traces carry the reversibility
    # field in the current schema (1.2.0).
    m4_stateless = reversibility_ratio(all_real, node_type="S")
    m4_stateful  = reversibility_ratio(all_real, node_type="F")
    m4_all       = reversibility_ratio(all_real)

    # ── 8. M5: Explanation completeness ──────────────────────────────────────
    # Computed on ALL traces (real + shadow) per the metric definition.
    # Old entries written before schema 1.2.0 may lack dag_pattern or
    # kernel_signals and will correctly reduce this metric.
    all_traces   = sl_all + sf_all
    m5_stateless = explanation_completeness(all_traces, node_type="S")
    m5_stateful  = explanation_completeness(all_traces, node_type="F")
    m5_all       = explanation_completeness(all_traces)

    # ── 9. M6: Unsafe-action suppression rate ─────────────────────────────────
    # Real-mode only.  Stateless track has no WASM sandbox or blocked_reason
    # field, so M6 is always 0.0 for stateless — correct by design.
    m6_stateless = unsafe_action_suppression_rate(all_real, node_type="S")
    m6_stateful  = unsafe_action_suppression_rate(all_real, node_type="F")
    m6_all       = unsafe_action_suppression_rate(all_real)

    # ── 10. Supporting distributions ─────────────────────────────────────────
    action_dist = {
        "stateless": dict(collections.Counter(t["action"] for t in sl_real)),
        "stateful":  dict(collections.Counter(t["action"] for t in sf_real)),
    }

    kernel_signals_seen = {
        "stateless": sorted({s for t in sl_real for s in (t.get("kernel_signals") or [])}),
        "stateful":  sorted({s for t in sf_real for s in (t.get("kernel_signals") or [])}),
    }

    fsm_state_dist = dict(collections.Counter(
        t.get("fsm_state") for t in sf_real if t.get("fsm_state")
    ))

    sf_divs    = _load_jsonl(_PATHS["stateful_div_log"])
    div_pairs  = dict(collections.Counter(
        f"{d.get('real_action', '?')}→{d.get('shadow_action', '?')}"
        for d in sf_divs
    ))

    # ── 11. Dynamic caveats ───────────────────────────────────────────────────
    caveats = []

    sl_q = quality["stateless"]
    sf_q = quality["stateful"]

    if sl_q["missing_node_type"] > 0:
        caveats.append(
            f"{sl_q['missing_node_type']} stateless entries lack node_type "
            "(written before schema 1.2.0). Excluded from per-track metrics; "
            "included in 'all' computations — may reduce M5 explanation_completeness.all."
        )
    if sf_q["missing_node_type"] > 0:
        caveats.append(
            f"{sf_q['missing_node_type']} stateful entries lack node_type "
            "(written before schema 1.2.0)."
        )
    if sl_q["missing_decision_timestamp"] > 0:
        caveats.append(
            f"{sl_q['missing_decision_timestamp']} stateless real entries have "
            "null decision_timestamp and are excluded from M3 MTTR computation."
        )
    if sf_q["missing_decision_timestamp"] > 0:
        caveats.append(
            f"{sf_q['missing_decision_timestamp']} stateful real entries have "
            "null decision_timestamp and are excluded from M3 MTTR computation."
        )
    if sl_q["missing_dag_pattern"] > 0:
        caveats.append(
            f"{sl_q['missing_dag_pattern']} stateless real entries lack dag_pattern; "
            "these fail the M5 explanation_completeness check."
        )
    if sl_q["missing_reversibility"] > 0:
        caveats.append(
            f"{sl_q['missing_reversibility']} stateless non-no_action entries have "
            "null reversibility; excluded from M4 denominator."
        )
    if not os.path.exists(_PATHS["stateless_div_log"]):
        caveats.append(
            "stateless/divergence_log.jsonl absent — M2 stateless divergence_rate "
            "is 0.0. Expected: stateless real and shadow paths use the same "
            "pure-threshold DAG (no FSM state), so they never diverge."
        )
    if m3_stateless["n_episodes"] == 0:
        caveats.append(
            "No complete fault→recovery episodes found in stateless real traces; "
            "M3 MTTR stateless = 0.0. This means either no container triggered an "
            "action, or no action was followed by a no_action in the trace window."
        )
    if m3_stateful["null_timestamp_count"] > 0 or m3_stateless["null_timestamp_count"] > 0:
        caveats.append(
            "MTTR uses decision_timestamp. Entries with null decision_timestamp are "
            "skipped; see mttr_null_ts_skipped in this report."
        )

    # ── 12. Assemble output ───────────────────────────────────────────────────
    metrics_out = {
        "M1_misclassification_rate": m1,
        "M2_divergence_rate": {
            "stateless": m2["stateless"],
            "stateful":  m2["stateful"],
            "overall":   m2["overall"],
        },
        "M3_mttr_seconds": {
            "all":       {"mean_s": m3_all["mean_s"],
                          "n_episodes": m3_all["n_episodes"]},
            "stateless": {"mean_s": m3_stateless["mean_s"],
                          "n_episodes": m3_stateless["n_episodes"]},
            "stateful":  {"mean_s": m3_stateful["mean_s"],
                          "n_episodes": m3_stateful["n_episodes"]},
        },
        "M3_mttr_per_container": {
            "stateless": m3_stateless["per_container"],
            "stateful":  m3_stateful["per_container"],
        },
        "M4_reversibility_ratio": {
            "all":       m4_all,
            "stateless": m4_stateless,
            "stateful":  m4_stateful,
        },
        "M5_explanation_completeness": {
            "all":       m5_all,
            "stateless": m5_stateless,
            "stateful":  m5_stateful,
        },
        "M6_unsafe_action_suppression_rate": {
            "all":       m6_all,
            "stateless": m6_stateless,
            "stateful":  m6_stateful,
        },
    }

    result = {
        "generated_at":              time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mttr_timestamp_field":      "decision_timestamp",
        "trace_quality":             quality,
        "action_distribution":       action_dist,
        "kernel_signals_observed":   kernel_signals_seen,
        "fsm_state_distribution":    fsm_state_dist,
        "divergence_action_pairs":   div_pairs,
        "metrics":                   metrics_out,
        "mttr_null_ts_skipped": {
            "stateless": m3_stateless["null_timestamp_count"],
            "stateful":  m3_stateful["null_timestamp_count"],
        },
        "caveats": caveats,
    }

    _print_table(metrics_out, quality)

    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)
    print(f"\n[real_metrics_report] Saved to {out_path}")

    return result


# ── Formatted output ──────────────────────────────────────────────────────────

def _print_table(metrics: dict, quality: dict) -> None:
    sl = quality["stateless"]
    sf = quality["stateful"]
    m2  = metrics["M2_divergence_rate"]
    m3  = metrics["M3_mttr_seconds"]
    m4  = metrics["M4_reversibility_ratio"]
    m5  = metrics["M5_explanation_completeness"]
    m6  = metrics["M6_unsafe_action_suppression_rate"]

    W = 68
    print()
    print("=" * W)
    print("  UnderCurrent — Real-Data Metric Report")
    print("  MTTR timestamp: decision_timestamp")
    print("=" * W)
    print(f"  Stateless: {sl['total']:>6} entries  "
          f"({sl['real']:>5} real / {sl['shadow']:>5} shadow"
          f"  |  {sl['missing_node_type']} missing node_type)")
    print(f"  Stateful:  {sf['total']:>6} entries  "
          f"({sf['real']:>5} real / {sf['shadow']:>5} shadow"
          f"  |  {sf['missing_node_type']} missing node_type)")
    print("-" * W)
    print(f"  {'Metric':<44}  {'Type S':>8}  {'Type F':>8}")
    print("-" * W)
    print(f"  {'M1  Misclassification rate':<44}  "
          f"{'(combined)':>8}  {metrics['M1_misclassification_rate']:>8.4f}")
    print(f"  {'M2  Divergence rate':<44}  "
          f"{m2['stateless']:>8.4f}  {m2['stateful']:>8.4f}")
    print(f"  {'M3  MTTR mean (seconds, decision_ts)':<44}  "
          f"{m3['stateless']['mean_s']:>8.4f}  {m3['stateful']['mean_s']:>8.4f}")
    print(f"  {'    MTTR episodes counted':<44}  "
          f"{m3['stateless']['n_episodes']:>8}  {m3['stateful']['n_episodes']:>8}")
    print(f"  {'M4  Reversibility ratio':<44}  "
          f"{m4['stateless']:>8.4f}  {m4['stateful']:>8.4f}")
    print(f"  {'M5  Explanation completeness':<44}  "
          f"{m5['stateless']:>8.4f}  {m5['stateful']:>8.4f}")
    print(f"  {'    (combined, all traces)':<44}  "
          f"{m5['all']:>8.4f}  {'':>8}")
    print(f"  {'M6  Unsafe-action suppression rate':<44}  "
          f"{m6['stateless']:>8.4f}  {m6['stateful']:>8.4f}")
    print("=" * W)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="UnderCurrent real-data metrics report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Reads stateless/traces.jsonl and stateful/traces.jsonl.\n"
            "Run the system first if these files do not exist:\n"
            "  python3 run.py\n"
        ),
    )
    parser.add_argument(
        "--out", default=_DEFAULT_OUT,
        help=f"Output JSON path (default: {_DEFAULT_OUT})",
    )
    args = parser.parse_args()
    run(out_path=args.out)


if __name__ == "__main__":
    main()
