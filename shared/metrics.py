# shared/metrics.py — UnderCurrent Research Evaluation Metrics
#
# Implements all 6 research metrics defined in the executive summary.
# All functions accept plain lists of trace dicts (as written by trace_logger.py)
# and/or file paths to JSONL logs.  No external dependencies beyond stdlib.
#
# Metric catalogue:
#   1. misclassification_rate          — S vs F fault classification accuracy
#   2. divergence_rate_from_traces     — shadow vs real action divergence
#   3. mttr                            — mean time to recovery (per node type)
#   4. reversibility_ratio             — fraction of reversible remediation actions
#   5. explanation_completeness        — fraction of fully-explained traces
#   6. unsafe_action_suppression_rate  — fraction blocked by policy/WASM gate
#   7. compute_all                     — run all metrics, return nested dict
#
# Usage (from repo root):
#   import sys; sys.path.insert(0, "shared")
#   from metrics import compute_all
#   report = compute_all(
#       stateless_trace_file="stateless/traces.jsonl",
#       stateful_trace_file="stateful/traces.jsonl",
#       stateless_div_log="stateless/divergence_log.jsonl",
#       stateful_div_log="stateful/divergence_log.jsonl",
#   )

import json
import os
import time


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_jsonl(path: str) -> list:
    """Load a JSONL file into a list of dicts. Returns [] if file is absent."""
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        rows = []
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return rows


def _real(traces: list) -> list:
    """Filter to real-mode traces only (mode == 'real')."""
    return [t for t in traces if t.get("mode") == "real"]


def _by_node_type(traces: list, node_type) -> list:
    """Filter traces to a specific node_type, or return all if node_type is None."""
    if node_type is None:
        return traces
    return [t for t in traces if t.get("node_type") == node_type]


# ── Metric 1: Misclassification Rate ──────────────────────────────────────────

# Kernel signals that are exclusively produced by the stateful eBPF probes.
# If these appear in an S-track trace, the workload was misclassified.
_F_ONLY_SIGNALS = frozenset({
    "blk_io_latency_high", "blk_io_latency_normal",
    "vfs_write_error", "vfs_read_error",
    "volume_mount_lost", "volume_mount_restored",
})


def misclassification_rate(stateless_traces: list, stateful_traces: list) -> float:
    """
    Fraction of all traces that show evidence of workload type misclassification.

    Detection rules (applied independently, counted once per trace):
      (a) S-track trace contains F-type kernel signals (stateful probe contamination).
      (b) S-track trace claims node_type == "F" (labelling error).
      (c) F-track trace claims node_type == "S" (labelling error).

    Returns a rate in [0.0, 1.0].
    """
    total = len(stateless_traces) + len(stateful_traces)
    if total == 0:
        return 0.0

    # Use a set of (track, index) pairs to avoid double-counting
    misclassified_indices = set()

    for i, t in enumerate(stateless_traces):
        signals = set(t.get("kernel_signals") or [])
        if signals & _F_ONLY_SIGNALS:
            misclassified_indices.add(("S", i))
        elif t.get("node_type") == "F":
            misclassified_indices.add(("S", i))

    for i, t in enumerate(stateful_traces):
        if t.get("node_type") == "S":
            misclassified_indices.add(("F", i))

    return round(len(misclassified_indices) / total, 4)


# ── Metric 2: Divergence Rate ──────────────────────────────────────────────────

def divergence_rate_from_traces(
    sl_traces: list,
    sf_traces: list,
    sl_div_log: str,
    sf_div_log: str,
) -> dict:
    """
    Fraction of real decisions that diverged from the shadow controller decision.

    Numerator:   entries in the divergence_log.jsonl files written by main.py.
    Denominator: count of real-mode trace entries per track.

    Args:
        sl_traces   — stateless trace dicts
        sf_traces   — stateful trace dicts
        sl_div_log  — path to stateless/divergence_log.jsonl
        sf_div_log  — path to stateful/divergence_log.jsonl

    Returns:
        {"stateless": float, "stateful": float, "overall": float}
    """
    sl_divs = _load_jsonl(sl_div_log)
    sf_divs = _load_jsonl(sf_div_log)

    sl_real_count = len(_real(sl_traces))
    sf_real_count = len(_real(sf_traces))

    sl_rate = round(len(sl_divs) / sl_real_count, 4) if sl_real_count else 0.0
    sf_rate = round(len(sf_divs) / sf_real_count, 4) if sf_real_count else 0.0

    total_real = sl_real_count + sf_real_count
    total_divs = len(sl_divs) + len(sf_divs)
    overall    = round(total_divs / total_real, 4) if total_real else 0.0

    return {"stateless": sl_rate, "stateful": sf_rate, "overall": overall}


# ── Metric 3: Mean Time To Recovery (MTTR) ────────────────────────────────────

def mttr(traces: list, node_type: str = None,
         timestamp_field: str = "trace_time") -> float:
    """
    Mean time from fault onset to recovery, in seconds.

    Algorithm per container (using real-mode traces only):
      1. Sort by timestamp_field.
      2. Fault onset  = first trace where action != 'no_action'.
      3. Recovery     = first subsequent trace where action == 'no_action'.
      4. MTTR sample  = recovery_timestamp - onset_timestamp.
      5. Average across all complete onset→recovery pairs found.

    timestamp_field: field to use for timing. Default "trace_time" (log-write
      time, set after action execution). Use "decision_timestamp" for real
      traces to measure time from when the decision was made, not when the
      log entry was written — more accurate when actions take wall-clock time
      to execute (e.g. docker restart).

    Traces with a null or zero value for timestamp_field are skipped.

    Containers that are faulted but never recover within the trace window
    are excluded from the average (not counted as infinite MTTR).

    node_type: "S", "F", or None (include all traces).
    Returns 0.0 if no complete fault/recovery pairs are found.
    """
    filtered = _by_node_type(_real(traces), node_type)

    by_container: dict = {}
    for t in filtered:
        c  = t.get("container")
        ts = t.get(timestamp_field)
        if c and ts:   # skip entries with missing/null timestamp
            by_container.setdefault(c, []).append(t)

    samples = []
    for c, events in by_container.items():
        events_sorted = sorted(events, key=lambda x: float(x.get(timestamp_field, 0)))
        onset_time = None
        for ev in events_sorted:
            action = ev.get("action", "no_action")
            tt     = float(ev.get(timestamp_field, 0))
            if onset_time is None and action != "no_action":
                onset_time = tt
            elif onset_time is not None and action == "no_action":
                samples.append(tt - onset_time)
                onset_time = None   # reset: detect subsequent fault cycles

    if not samples:
        return 0.0
    return round(sum(samples) / len(samples), 4)


# ── Metric 4: Reversibility Ratio ─────────────────────────────────────────────

def reversibility_ratio(traces: list, node_type: str = None) -> float:
    """
    Fraction of real non-no_action decisions that used a reversible action.

    Numerator:   real-mode decisions where reversibility == 'reversible'.
    Denominator: all real-mode decisions where action != 'no_action' and
                 reversibility field is present.

    Shadow traces are excluded. Decisions without a reversibility field
    are excluded from both numerator and denominator.

    Returns 0.0 if no qualifying decisions exist.
    """
    filtered = _by_node_type(_real(traces), node_type)
    active   = [
        t for t in filtered
        if t.get("action") != "no_action"
        and t.get("reversibility") is not None
    ]
    if not active:
        return 0.0
    reversible = sum(1 for t in active if t["reversibility"] == "reversible")
    return round(reversible / len(active), 4)


# ── Metric 5: Explanation Completeness ────────────────────────────────────────

# Fields required for a trace to count as fully explained.
_EXPLANATION_REQUIRED = (
    "why",             # human-readable decision reason
    "action",          # action taken
    "score",           # confidence score
    "kernel_signals",  # list — empty is OK only for no_action
    "dag_pattern",     # matched DAG pattern name
)


def explanation_completeness(traces: list, node_type: str = None) -> float:
    """
    Fraction of traces with all required explanation fields populated.

    A field is satisfied when it is present and:
      - Not None
      - Not empty string ""
      - Not empty list [] — UNLESS action is 'no_action' (no signals is expected then)

    Returns 0.0 if no traces.
    """
    filtered = _by_node_type(traces, node_type)
    if not filtered:
        return 0.0

    def _complete(t: dict) -> bool:
        for field in _EXPLANATION_REQUIRED:
            val = t.get(field)
            if val is None:
                return False
            if isinstance(val, str) and val == "":
                return False
            if isinstance(val, list) and len(val) == 0:
                # Empty kernel_signals is only acceptable for no_action decisions
                if t.get("action") != "no_action":
                    return False
        return True

    complete = sum(1 for t in filtered if _complete(t))
    return round(complete / len(filtered), 4)


# ── Metric 6: Unsafe-Action Suppression Rate ──────────────────────────────────

def unsafe_action_suppression_rate(traces: list, node_type: str = None) -> float:
    """
    Fraction of real decisions that were suppressed by the WASM / policy gate.

    A decision is "suppressed" if:
      - wasm_blocked == True   (stateful track — WASM sandbox rejected the action), OR
      - blocked_reason is non-null and non-empty (stateful FSM/reversibility gate), OR
      - the action field was downgraded to 'no_action' AND blocked_reason is set.

    Shadow-mode traces are excluded (execution suppression is not meaningful there).

    Returns 0.0 if no real decisions.
    """
    filtered = _by_node_type(_real(traces), node_type)
    if not filtered:
        return 0.0

    suppressed = sum(
        1 for t in filtered
        if t.get("wasm_blocked") is True
        or (t.get("blocked_reason") not in (None, ""))
    )
    return round(suppressed / len(filtered), 4)


# ── Metric 7: compute_all ─────────────────────────────────────────────────────

def compute_all(
    stateless_trace_file: str,
    stateful_trace_file:  str,
    stateless_div_log:    str,
    stateful_div_log:     str,
    mttr_timestamp_field: str = "trace_time",
) -> dict:
    """
    Load all trace and divergence files, compute every research metric.

    Returns:
        {
          "misclassification_rate":        float,
          "divergence_rate":               {"stateless": float, "stateful": float, "overall": float},
          "mttr":                          {"all": float, "stateless": float, "stateful": float},
          "reversibility_ratio":           {"all": float, "stateless": float, "stateful": float},
          "explanation_completeness":      {"all": float, "stateless": float, "stateful": float},
          "unsafe_action_suppression_rate":{"all": float, "stateless": float, "stateful": float},
        }
    """
    sl = _load_jsonl(stateless_trace_file)
    sf = _load_jsonl(stateful_trace_file)
    all_traces = sl + sf

    return {
        "misclassification_rate": misclassification_rate(sl, sf),

        "divergence_rate": divergence_rate_from_traces(
            sl, sf, stateless_div_log, stateful_div_log
        ),

        "mttr": {
            "all":       mttr(all_traces, timestamp_field=mttr_timestamp_field),
            "stateless": mttr(all_traces, node_type="S", timestamp_field=mttr_timestamp_field),
            "stateful":  mttr(all_traces, node_type="F", timestamp_field=mttr_timestamp_field),
        },

        "reversibility_ratio": {
            "all":       reversibility_ratio(all_traces),
            "stateless": reversibility_ratio(all_traces, node_type="S"),
            "stateful":  reversibility_ratio(all_traces, node_type="F"),
        },

        "explanation_completeness": {
            "all":       explanation_completeness(all_traces),
            "stateless": explanation_completeness(all_traces, node_type="S"),
            "stateful":  explanation_completeness(all_traces, node_type="F"),
        },

        "unsafe_action_suppression_rate": {
            "all":       unsafe_action_suppression_rate(all_traces),
            "stateless": unsafe_action_suppression_rate(all_traces, node_type="S"),
            "stateful":  unsafe_action_suppression_rate(all_traces, node_type="F"),
        },
    }


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import pprint

    now = time.time()

    # ── Synthetic stateless traces ─────────────────────────────────────────────
    sl_traces = [
        # nginx: fault onset at now-20 (restart), recovery at now (no_action)
        {"container": "nginx", "node_type": "S", "mode": "real", "action": "restart",
         "score": 0.90, "why": "score high", "trace_time": now - 20,
         "kernel_signals": ["process_exit"], "dag_pattern": "process_failure",
         "reversibility": "reversible", "wasm_blocked": False, "blocked_reason": None},
        {"container": "nginx", "node_type": "S", "mode": "real", "action": "no_action",
         "score": 0.20, "why": "score low",  "trace_time": now,
         "kernel_signals": [], "dag_pattern": "nominal",
         "reversibility": "reversible", "wasm_blocked": False, "blocked_reason": None},
        # pyapp: shadow mode — excluded from MTTR and suppression
        {"container": "pyapp", "node_type": "S", "mode": "shadow", "action": "restart",
         "score": 0.85, "why": "shadow",     "trace_time": now,
         "kernel_signals": ["tcp_connect_fail"], "dag_pattern": "network_degraded",
         "reversibility": "reversible", "wasm_blocked": False, "blocked_reason": None},
    ]

    # ── Synthetic stateful traces ──────────────────────────────────────────────
    sf_traces = [
        # postgres: fault onset at now-30 (flush), recovery at now (no_action)
        {"container": "postgres", "node_type": "F", "mode": "real",
         "action": "flush_io_queue", "score": 0.65, "why": "io degraded",
         "trace_time": now - 30,
         "kernel_signals": ["blk_io_latency_high"], "dag_pattern": "io_degradation_minor",
         "reversibility": "reversible", "wasm_blocked": False, "blocked_reason": None,
         "fsm_state": "Degraded"},
        {"container": "postgres", "node_type": "F", "mode": "real",
         "action": "no_action", "score": 0.20, "why": "recovered",
         "trace_time": now,
         "kernel_signals": [], "dag_pattern": "nominal",
         "reversibility": "reversible", "wasm_blocked": False, "blocked_reason": None,
         "fsm_state": "Healthy"},
        # etcd: wasm-blocked escalate — counts as suppressed
        {"container": "etcd", "node_type": "F", "mode": "real",
         "action": "no_action", "score": 0.97, "why": "wasm blocked",
         "trace_time": now,
         "kernel_signals": ["volume_mount_lost"], "dag_pattern": "io_degradation_critical",
         "reversibility": "conditional", "wasm_blocked": True,
         "blocked_reason": "WASM sandbox: escalate blocked",
         "fsm_state": "Healthy"},
    ]

    # Write temp trace files
    def _write_tmp(rows):
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.close()
        return f.name

    sl_file = _write_tmp(sl_traces)
    sf_file = _write_tmp(sf_traces)
    sl_div  = _write_tmp([])    # no divergences in this test
    sf_div  = _write_tmp([])

    result = compute_all(sl_file, sf_file, sl_div, sf_div)
    pprint.pprint(result)

    # ── Assertions ────────────────────────────────────────────────────────────
    assert result["misclassification_rate"] == 0.0, result["misclassification_rate"]
    assert result["divergence_rate"]["stateless"] == 0.0
    assert result["divergence_rate"]["stateful"]  == 0.0
    assert result["divergence_rate"]["overall"]   == 0.0

    # nginx: 20s recovery; postgres: 30s recovery → MTTR all = 25.0s
    assert result["mttr"]["all"]       == 25.0, result["mttr"]["all"]
    assert result["mttr"]["stateless"] == 20.0, result["mttr"]["stateless"]
    assert result["mttr"]["stateful"]  == 30.0, result["mttr"]["stateful"]

    # Real non-no_action decisions: nginx restart (reversible), postgres flush (reversible)
    # etcd is no_action (wasm blocked it) — no reversibility field applies
    # reversibility_ratio all = 2/2 = 1.0
    assert result["reversibility_ratio"]["all"]       == 1.0, result["reversibility_ratio"]
    assert result["reversibility_ratio"]["stateless"] == 1.0
    assert result["reversibility_ratio"]["stateful"]  == 1.0

    # explanation_completeness: all 6 traces have all required fields
    assert result["explanation_completeness"]["all"] == 1.0, result["explanation_completeness"]

    # suppression: etcd has wasm_blocked=True → 1 suppressed out of 5 real decisions
    # (sl real: nginx×2; sf real: postgres×2 + etcd×1 = 5 total real, shadow excluded)
    assert result["unsafe_action_suppression_rate"]["all"] == round(1 / 5, 4), \
        result["unsafe_action_suppression_rate"]
    assert result["unsafe_action_suppression_rate"]["stateless"] == 0.0
    assert result["unsafe_action_suppression_rate"]["stateful"]  == round(1 / 3, 4)

    # misclassification: inject a bad trace to verify detection
    bad_sl = sl_traces + [
        {"container": "bad", "node_type": "S", "mode": "real",
         "kernel_signals": ["vfs_write_error"], "action": "no_action",
         "score": 0.1, "why": "low"}
    ]
    rate = misclassification_rate(bad_sl, sf_traces)
    assert rate > 0.0, "Expected non-zero misclassification for F-signal in S trace"

    for f in [sl_file, sf_file, sl_div, sf_div]:
        os.unlink(f)

    print("\nAll self-tests passed.")
