# shared/eval_runner.py — UnderCurrent Evaluation Runner
#
# Computes all research metrics from actual trace files and saves a timestamped
# results snapshot to shared/eval_results.json.
#
# GROUNDING: Every number in the output is derived from one of these files:
#   stateful/traces.jsonl         — stateful real + shadow decisions
#   stateless/traces.jsonl        — stateless real + shadow decisions
#   stateful/divergence_log.jsonl  — shadow/real divergence records (stateful track)
#   stateless/divergence_log.jsonl — shadow/real divergence records (stateless track,
#                                    written only when run with --mode divergence)
#
# stateless traces include a 'reversibility' field; reversibility_ratio is measured.
#
# Usage (from repo root):
#   python3 shared/eval_runner.py
#   python3 shared/eval_runner.py --out path/to/results.json
#   python3 shared/eval_runner.py --data-dir data/phase2   # reproduce paper Phase 2 metrics

import json
import os
import sys
import time
import argparse

# Make shared/ importable regardless of working directory
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from metrics import compute_all, _load_jsonl

_REPO_ROOT = os.path.dirname(_here)

# Canonical file paths relative to repo root
_PATHS = {
    "stateful_traces":    os.path.join(_REPO_ROOT, "stateful",  "traces.jsonl"),
    "stateless_traces":   os.path.join(_REPO_ROOT, "stateless", "traces.jsonl"),
    "stateful_div_log":   os.path.join(_REPO_ROOT, "stateful",  "divergence_log.jsonl"),
    "stateless_div_log":  os.path.join(_REPO_ROOT, "stateless", "divergence_log.jsonl"),
}

_DEFAULT_OUT = os.path.join(_here, "eval_results.json")


def _file_info(path: str) -> dict:
    """Record line count and modification time for provenance."""
    if not os.path.exists(path):
        return {"exists": False, "lines": 0, "mtime": None}
    mtime = os.path.getmtime(path)
    with open(path) as f:
        lines = sum(1 for ln in f if ln.strip())
    return {
        "exists":  True,
        "lines":   lines,
        "mtime":   mtime,
        "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)),
    }


def _count_by_mode(traces: list) -> dict:
    real   = sum(1 for t in traces if t.get("mode") == "real")
    shadow = sum(1 for t in traces if t.get("mode") == "shadow")
    return {"total": len(traces), "real": real, "shadow": shadow}


def run(out_path: str = _DEFAULT_OUT, data_dir: str = None) -> dict:
    """
    Load trace files, compute all metrics, annotate with provenance metadata,
    and write results to out_path.

    Args:
        out_path:  path to write the JSON results file.
        data_dir:  if set, override default trace file paths with files from
                   this directory (e.g. 'data/phase2' to reproduce paper metrics).

    Returns the full results dict.
    """
    paths = dict(_PATHS)
    if data_dir:
        d = os.path.abspath(data_dir)
        paths["stateful_traces"]  = os.path.join(d, "stateful_traces.jsonl")
        paths["stateless_traces"] = os.path.join(d, "stateless_traces.jsonl")
        paths["stateful_div_log"] = os.path.join(d, "stateful_divergence_log.jsonl")
        paths["stateless_div_log"] = os.path.join(d, "stateless_divergence_log.jsonl")

    # ── Source inventory ───────────────────────────────────────────────────────
    sources = {name: _file_info(path) for name, path in paths.items()}

    # ── Load traces for count metadata ────────────────────────────────────────
    sf = _load_jsonl(paths["stateful_traces"])
    sl = _load_jsonl(paths["stateless_traces"])

    trace_counts = {
        "stateful":  _count_by_mode(sf),
        "stateless": _count_by_mode(sl),
    }

    # Action distribution (real-mode only) — used to characterise workloads
    import collections
    sf_real = [t for t in sf if t.get("mode") == "real"]
    sl_real = [t for t in sl if t.get("mode") == "real"]

    action_dist = {
        "stateful":  dict(collections.Counter(t["action"] for t in sf_real)),
        "stateless": dict(collections.Counter(t["action"] for t in sl_real)),
    }

    kernel_signals = {
        "stateful": sorted({
            s for t in sf_real for s in (t.get("kernel_signals") or [])
        }),
        "stateless": sorted({
            s for t in sl_real for s in (t.get("kernel_signals") or [])
        }),
    }

    # FSM state distribution (stateful real only)
    fsm_dist = dict(collections.Counter(
        t.get("fsm_state") for t in sf_real if t.get("fsm_state")
    ))

    # Divergence log details
    sf_divs = _load_jsonl(paths["stateful_div_log"])
    div_action_pairs = dict(collections.Counter(
        f"{d['real_action']}→{d['shadow_action']}" for d in sf_divs
    ))

    # ── Compute all 6 research metrics ────────────────────────────────────────
    metrics = compute_all(
        stateless_trace_file=paths["stateless_traces"],
        stateful_trace_file=paths["stateful_traces"],
        stateless_div_log=paths["stateless_div_log"],
        stateful_div_log=paths["stateful_div_log"],
    )

    # ── Caveats — structural gaps in the data (computed dynamically) ──────────
    caveats = []

    sl_missing_node_type = sum(1 for t in sl if not t.get("node_type"))
    sf_missing_node_type = sum(1 for t in sf if not t.get("node_type"))
    if sl_missing_node_type > 0:
        caveats.append(
            f"{sl_missing_node_type} stateless entries lack node_type (written before "
            "schema 1.2.0). These are excluded from per-track metrics but included in "
            "'all' computations, which may lower explanation_completeness.all."
        )
    if sf_missing_node_type > 0:
        caveats.append(
            f"{sf_missing_node_type} stateful entries lack node_type (written before "
            "schema 1.2.0)."
        )

    sl_missing_dag = sum(
        1 for t in sl if t.get("mode") == "real" and not t.get("dag_pattern")
    )
    if sl_missing_dag > 0:
        caveats.append(
            f"{sl_missing_dag} stateless real entries lack dag_pattern; these entries "
            "fail the explanation_completeness check and lower M5."
        )

    if not os.path.exists(paths["stateless_div_log"]):
        caveats.append(
            "stateless/divergence_log.jsonl does not exist — stateless divergence_rate "
            "is 0.0. Run with --mode divergence to generate it."
        )

    caveats.append(
        "stateless traces include real system processes (kernel process_exit events "
        "from non-container processes) captured by the eBPF listener. Stateless MTTR "
        "reflects real-system process lifecycle, not injected fault recovery."
    )
    caveats.append(
        "mttr uses trace_time (log-write time). For decision_timestamp-based MTTR "
        "on real traces, run shared/real_metrics_report.py instead."
    )

    # ── Assemble output ────────────────────────────────────────────────────────
    result = {
        "generated_at":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources":           sources,
        "trace_counts":      trace_counts,
        "action_distribution": action_dist,
        "kernel_signals_observed": kernel_signals,
        "fsm_state_distribution_stateful_real": fsm_dist,
        "divergence_action_pairs": div_action_pairs,
        "metrics":           metrics,
        "caveats":           caveats,
    }

    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[eval_runner] Results written to {out_path}")
    print(f"[eval_runner] Sources:")
    for name, info in sources.items():
        status = f"{info['lines']} lines" if info["exists"] else "NOT FOUND"
        print(f"  {name:<24} {status}")
    print(f"\n[eval_runner] Metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UnderCurrent evaluation runner")
    parser.add_argument("--out", default=_DEFAULT_OUT,
                        help=f"Output JSON path (default: {_DEFAULT_OUT})")
    parser.add_argument(
        "--data-dir", default=None, metavar="DIR",
        help="Override trace file directory. Use 'data/phase2' to reproduce paper "
             "Phase 2 metrics from the committed snapshot.",
    )
    args = parser.parse_args()
    run(out_path=args.out, data_dir=args.data_dir)
