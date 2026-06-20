# shared/trace_schema.py — UnderCurrent Shared Trace Schema Contract
#
# Defines the field catalogue for both Stateless (S) and Stateful (F) decision traces.
# Schema version 1.3.0 — additive extension only; base fields are frozen.
#
# RULE: Never remove or rename BASE_FIELDS — doing so breaks both track loggers,
#       both dashboards, and any downstream consumer.
#       STATEFUL_FIELDS are absent in stateless traces; consumers must tolerate
#       missing keys gracefully.
#
# stateless/trace_logger.py produces all BASE_FIELDS + COMMON_EXTENSION_FIELDS
# (including reversibility) by design and does not need to import this file.
# stateful/trace_logger.py imports SCHEMA_VERSION from here.
# This file exists to make the schema contract explicit and machine-readable.

# ── Base fields (frozen — present in all traces from both tracks) ──────────────
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

# ── Common extension fields — present in BOTH S and F traces (added 1.2.0) ────
# node_type, kernel_signals, dag_pattern, and reversibility are produced by both
# stateless/reconcile.py and stateful/reconcile.py.
COMMON_EXTENSION_FIELDS = [
    "node_type",        # str       — "S" for stateless traces, "F" for stateful traces
    "kernel_signals",   # list[str] — kernel observation names that triggered the decision
    "dag_pattern",      # str       — matched DAG fault-pattern name
    "reversibility",    # str       — "reversible" | "conditional" | "irreversible"
]

# ── Stateful-only extension fields (absent in stateless traces) ────────────────
# Any consumer reading a mixed trace file must handle missing keys gracefully.
STATEFUL_FIELDS = [
    "fsm_state",        # str       — FSM state of the container BEFORE this decision
    "fsm_state_after",  # str       — FSM state AFTER all transitions this cycle
    "fsm_transition",   # str|None  — transition applied this cycle, e.g. "Healthy→Degraded"
    "blocked_reason",   # str|None  — why a higher-severity action was blocked (if any)
    "wasm_blocked",     # bool      — True if WASM sandbox blocked the action
    "wasm_reason",      # str|None  — reason string from WASM sandbox (if blocked)
]

ALL_FIELDS = BASE_FIELDS + COMMON_EXTENSION_FIELDS + STATEFUL_FIELDS

SCHEMA_VERSION = "1.3.0"
# Version semantics:
#   major — incompatible change (rename / remove field)  → requires review before merge
#   minor — new field group added                        → backward compatible
#   patch — documentation / comment update only
# 1.3.0 — added fsm_state_after, wasm_blocked, wasm_reason to STATEFUL_FIELDS
#          (these were written by stateful/trace_logger.py since 1.2.0 but absent
#           from the schema catalogue — now formally declared)
# 1.2.0 — node_type, kernel_signals, dag_pattern promoted to COMMON_EXTENSION_FIELDS
#          (now present in both S and F traces)
