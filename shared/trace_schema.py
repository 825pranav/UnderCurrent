# shared/trace_schema.py — UnderCurrent Shared Trace Schema Contract
#
# Defines the field catalogue for both Stateless (S) and Stateful (F) decision traces.
# Schema version 1.1.0 — additive extension only; base fields are frozen.
#
# RULE: Never remove or rename BASE_FIELDS — doing so breaks Pranav's trace_logger.py,
#       both dashboards, and any downstream consumer.
#       Only extend STATEFUL_FIELDS (absent in stateless traces → consumers must tolerate
#       unknown fields gracefully).
#
# BACKWARD COMPATIBILITY NOTE: This file is imported by stateful/trace_logger.py.
# Pranav's stateless/trace_logger.py does NOT need to import this file —
# it already produces all BASE_FIELDS by design. This file exists purely
# to make the contract explicit and machine-readable.

# ── Base fields — Pranav's established schema (frozen, do not change) ─────────
BASE_FIELDS = [
    "trace_time",           # float  — wall-clock time of log write (unix timestamp)
    "container",            # str    — container / workload identifier
    "score",                # float  — confidence risk score (0.0–1.0)
    "action",               # str    — action token ("restart", "reschedule", "no_action", …)
    "mode",                 # str    — "real" | "shadow"
    "why",                  # str    — human-readable explanation of the decision
    "executed",             # bool|None — execution result; None in shadow mode
    "stdout",               # str    — action stdout
    "stderr",               # str    — action stderr
    "decision_timestamp",   # float  — when reconcile produced the decision
    "action_timestamp",     # float  — when action.execute() was called
]

# ── Stateful extension fields (present in Type-F traces only) ─────────────────
# Absent in stateless traces.  Any consumer that reads a mixed trace file must
# handle missing keys gracefully (e.g. dict.get("fsm_state") → None is fine).
STATEFUL_FIELDS = [
    "node_type",        # str       — always "F" for stateful traces
    "fsm_state",        # str       — FSM state of the container BEFORE this decision
    "fsm_transition",   # str|None  — transition applied this cycle, e.g. "Healthy→Degraded"
    "reversibility",    # str       — "reversible" | "conditional" | "irreversible"
    "kernel_signals",   # list[str] — kernel observation names that triggered the decision
    "dag_pattern",      # str       — matched DAG fault-pattern name
    "blocked_reason",   # str|None  — why a higher-severity action was blocked (if any)
]

ALL_FIELDS = BASE_FIELDS + STATEFUL_FIELDS

SCHEMA_VERSION = "1.1.0"
# Version semantics:
#   major — incompatible change (rename / remove field)  → requires Pranav sign-off
#   minor — new optional field added to STATEFUL_FIELDS  → backward compatible
#   patch — documentation / comment update only
