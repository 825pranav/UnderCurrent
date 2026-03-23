# fsm.py — F5a: Container State Machine Enforcement
#
# Enforces the formal state machine mandated by the abstract for stateful (Type F) nodes.
# All remediation actions from reconcile.py are FSM-gated: out-of-sequence actions
# are rejected or downgraded before execution.
#
# Formal FSM:
#   Healthy → Degraded → Audited → Repairing → Recovered → Healthy
#
# State semantics:
#   Healthy    — nominal operation; no remediation needed
#   Degraded   — confidence score crossed threshold; degradation confirmed
#   Audited    — minor remediation (flush) attempted; under close observation
#   Repairing  — major remediation (checkpoint-restart) in progress
#   Recovered  — repair completed; monitoring for stability confirmation
#
# Transition triggers:
#   degrade        — score >= threshold, from Healthy or Recovered
#   audit          — flush_io_queue dispatched; Degraded → Audited
#   approve_repair — checkpoint_and_restart dispatched; Audited → Repairing
#   recover        — repair action succeeded; Repairing → Recovered
#   heal           — score drops below threshold from Recovered; Recovered → Healthy
#   re_degrade     — re-degradation (handled by "degrade" trigger from Recovered/Audited)

import time

STATES = ("Healthy", "Degraded", "Audited", "Repairing", "Recovered")

# (from_state, trigger) → to_state
# Any key not in this map is an INVALID transition — apply() returns None.
TRANSITIONS: dict = {
    ("Healthy",   "degrade"):         "Degraded",
    ("Degraded",  "audit"):           "Audited",
    ("Degraded",  "degrade"):         "Degraded",    # score re-confirmed; stays Degraded
    ("Audited",   "approve_repair"):  "Repairing",
    ("Audited",   "degrade"):         "Degraded",    # worsened while auditing → step back
    ("Repairing", "recover"):         "Recovered",
    ("Repairing", "degrade"):         "Degraded",    # repair made things worse → restart path
    ("Recovered", "heal"):            "Healthy",
    ("Recovered", "degrade"):         "Degraded",    # re-degradation after recovery
}


class ContainerFSM:
    """
    Per-container FSM.  One instance lives for the full lifetime of main.py
    and is passed into reconcile() each cycle so every decision is FSM-aware.

    Thread-safety: not internally locked — reconcile loop runs in a single thread.
    """

    def __init__(self):
        # { container_name: state_name }
        self._states: dict = {}
        # { container_name: [transition_record, ...] }
        self._history: dict = {}

    def state(self, container: str) -> str:
        """Return current FSM state.  New containers start as Healthy."""
        return self._states.get(container, "Healthy")

    def apply(self, container: str, trigger: str):
        """
        Attempt to apply a trigger to the container's FSM.

        Returns (from_state, to_state) tuple if the transition is valid.
        Returns None if the (state, trigger) pair is not in the transition table
        — callers must treat None as a rejected / no-op transition.
        """
        current  = self.state(container)
        to_state = TRANSITIONS.get((current, trigger))
        if to_state is None:
            return None
        self._states[container] = to_state
        record = {
            "from":    current,
            "trigger": trigger,
            "to":      to_state,
            "time":    time.time(),
        }
        self._history.setdefault(container, []).append(record)
        return (current, to_state)

    def can_apply(self, container: str, trigger: str) -> bool:
        """True if the trigger is valid for the container's current state."""
        return (self.state(container), trigger) in TRANSITIONS

    def snapshot(self) -> dict:
        """{ container: current_state } — used by dashboard and trace logger."""
        return dict(self._states)

    def history(self, container: str) -> list:
        """Full transition log for a container."""
        return list(self._history.get(container, []))


# --- Self-test ---
if __name__ == "__main__":
    fsm = ContainerFSM()

    # Full happy path: Healthy → Degraded → Audited → Repairing → Recovered → Healthy
    assert fsm.state("postgres") == "Healthy"

    r = fsm.apply("postgres", "degrade")
    assert r == ("Healthy", "Degraded"), r
    assert fsm.state("postgres") == "Degraded"

    r = fsm.apply("postgres", "audit")
    assert r == ("Degraded", "Audited"), r

    r = fsm.apply("postgres", "approve_repair")
    assert r == ("Audited", "Repairing"), r

    r = fsm.apply("postgres", "recover")
    assert r == ("Repairing", "Recovered"), r

    r = fsm.apply("postgres", "heal")
    assert r == ("Recovered", "Healthy"), r

    # Invalid transition: Healthy → Repairing directly must be rejected
    r = fsm.apply("postgres", "approve_repair")
    assert r is None, "Should reject invalid transition"
    assert fsm.state("postgres") == "Healthy", "State must not change on rejection"

    # Re-degradation from Recovered
    fsm.apply("postgres", "degrade")           # Healthy → Degraded
    fsm.apply("postgres", "audit")             # Degraded → Audited
    fsm.apply("postgres", "approve_repair")    # Audited  → Repairing
    fsm.apply("postgres", "recover")           # Repairing → Recovered
    r = fsm.apply("postgres", "degrade")       # Recovered → Degraded (re-degradation)
    assert r == ("Recovered", "Degraded"), r

    # Snapshot
    snap = fsm.snapshot()
    assert snap["postgres"] == "Degraded"

    # can_apply
    assert fsm.can_apply("postgres", "audit") is True
    assert fsm.can_apply("postgres", "approve_repair") is False  # must be Audited first

    print("Transition history:", fsm.history("postgres"))
    print("\nAll self-tests passed.")
