# shared/constants.py — UnderCurrent Shared Threshold Constants
#
# Single source of truth for thresholds used by both tracks.
# Prevents drift between stateless/reconcile.py and stateful/reconcile.py.
#
# Import in either track:
#   import sys; sys.path.insert(0, "../shared")
#   from constants import RESTART_THRESHOLD, RESCHEDULE_THRESHOLD
#
# Schema version alignment: see shared/trace_schema.py

# ── Shared decision thresholds ────────────────────────────────────────────────
# Stateless uses:  RESTART_THRESHOLD, RESCHEDULE_THRESHOLD
# Stateful uses:   REPAIR_THRESHOLD (== RESTART_THRESHOLD),
#                  ESCALATE_THRESHOLD (== RESCHEDULE_THRESHOLD)

RESTART_THRESHOLD    = 0.80   # stateless: restart container
RESCHEDULE_THRESHOLD = 0.95   # stateless: reschedule to new node

REPAIR_THRESHOLD     = 0.80   # stateful alias — checkpoint_and_restart
ESCALATE_THRESHOLD   = 0.95   # stateful alias — escalate

FLUSH_THRESHOLD      = 0.50   # stateful only — flush_io_queue (minor remediation)
DEGRADE_THRESHOLD    = 0.50   # stateful only — FSM degrade trigger

# ── Sliding window ────────────────────────────────────────────────────────────
WINDOW_SECONDS = 60           # both tracks use 60s event window

# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA_VERSION = "1.2.0"      # keep in sync with shared/trace_schema.py
