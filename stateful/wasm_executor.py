# wasm_executor.py — WASM Policy Sandbox for Stateful Actions
#
# Loads each action's WAT source, compiles it to a WASM module via wasmtime,
# and exposes policy_check() — the independent sandbox enforcement point.
#
# This is the implementation of the research paper's claim:
#   "The system enforces policy-constrained actuation via sandboxed WASM modules."
#   "Actions are rejected if they violate the [FSM] sequence."
#
# Each .wat module exports exactly one function:
#   policy_check(fsm_state: i32, score: f32) -> i32
#   Returns 1 (allowed) or 0 (blocked).
#
# The WASM sandbox is a SECOND, INDEPENDENT gate — it runs AFTER the Python-level
# FSM transitions in reconcile.py have already advanced the FSM state.  If the
# Python gate were somehow bypassed, the WASM check would still block the action.
#
# FSM state encoding (must match stateful/fsm.py state names):
#   Healthy=0  Degraded=1  Audited=2  Repairing=3  Recovered=4
#
# Dependency:
#   pip install wasmtime
#
# Usage:
#   from wasm_executor import policy_check
#   allowed, reason = policy_check("flush_io_queue", "Degraded", 0.65)

import os

import wasmtime

# ── FSM state → integer encoding ──────────────────────────────────────────────
FSM_CODE: dict = {
    "Healthy":   0,
    "Degraded":  1,
    "Audited":   2,
    "Repairing": 3,
    "Recovered": 4,
}

# ── Action → WAT file mapping ─────────────────────────────────────────────────
_WAT_DIR = os.path.join(os.path.dirname(__file__), "wasm")

_ACTION_WAT: dict = {
    "no_action":              "no_action.wat",
    "flush_io_queue":         "flush_io_queue.wat",
    "checkpoint_and_restart": "checkpoint_restart.wat",
    "escalate":               "escalate.wat",
}

# ── Module cache (compiled once; Module objects are engine-scoped and thread-safe) ──
_engine  = wasmtime.Engine()
_modules: dict = {}

# Pre-compile all WAT modules at import time so the first policy_check() call
# does not include JIT compilation latency in overhead measurements.
def _precompile_all() -> None:
    for action in _ACTION_WAT:
        try:
            _get_module(action)
        except Exception as e:
            print(f"[wasm] WARNING: precompile failed for {action!r}: {e}", flush=True)

_precompile_all()


def _get_module(action: str) -> "wasmtime.Module":
    """
    Load and compile the WAT module for the given action.
    Compiled Module is cached so compilation only happens once per process.
    """
    if action not in _modules:
        wat_file = _ACTION_WAT.get(action)
        if wat_file is None:
            raise ValueError(f"No WASM module registered for action: {action!r}")
        wat_path = os.path.join(_WAT_DIR, wat_file)
        with open(wat_path) as f:
            wat_src = f.read()
        _modules[action] = wasmtime.Module(_engine, wat_src)
    return _modules[action]


def policy_check(action: str, fsm_state: str, score: float) -> tuple:
    """
    Run the WASM sandbox policy check for (action, fsm_state, score).

    Each call creates a fresh Store + Instance from the cached compiled Module.
    This keeps the sandbox stateless — no mutable state leaks between calls.

    Args:
        action:    action name (must match a key in _ACTION_WAT)
        fsm_state: FSM state name at dispatch time (post Python-level transitions)
        score:     confidence score (0.0–1.0)

    Returns:
        (allowed: bool, reason: str)

    Raises:
        ValueError: if action has no registered WAT module
        FileNotFoundError: if the WAT file is missing
    """
    fsm_code = FSM_CODE.get(fsm_state)
    if fsm_code is None:
        return False, f"WASM sandbox: unknown FSM state {fsm_state!r}"

    module  = _get_module(action)
    store   = wasmtime.Store(_engine)
    inst    = wasmtime.Instance(store, module, [])
    fn      = inst.exports(store)["policy_check"]
    allowed = fn(store, fsm_code, float(score))

    if allowed:
        return True, "WASM sandbox: policy satisfied"
    return False, (
        f"WASM sandbox: {action!r} blocked — "
        f"FSM={fsm_state!r} (code={fsm_code}), score={score:.3f}"
    )


# ── Unsafe-action suppression rate helper ────────────────────────────────────
# Used by main.py or dashboard to compute the research metric:
#   suppression_rate = wasm_blocked_count / total_decisions

def suppression_rate(traces: list) -> float:
    """
    Compute the Unsafe-action Suppression Rate from a list of trace dicts.
    Returns fraction of decisions blocked by the WASM sandbox (0.0–1.0).
    """
    total   = len(traces)
    blocked = sum(1 for t in traces if t.get("wasm_blocked") is True)
    return round(blocked / total, 4) if total > 0 else 0.0


# --- Self-test ---
if __name__ == "__main__":
    cases = [
        # (action, fsm_state, score, expect_allowed)
        # flush_io_queue — valid states
        ("flush_io_queue",         "Degraded",  0.65, True),
        ("flush_io_queue",         "Audited",   0.55, True),
        # flush_io_queue — blocked states
        ("flush_io_queue",         "Healthy",   0.65, False),
        ("flush_io_queue",         "Repairing", 0.65, False),
        ("flush_io_queue",         "Recovered", 0.65, False),
        # flush_io_queue — score below threshold
        ("flush_io_queue",         "Degraded",  0.49, False),

        # checkpoint_and_restart — valid states
        ("checkpoint_and_restart", "Audited",   0.85, True),
        ("checkpoint_and_restart", "Repairing", 0.82, True),
        # checkpoint_and_restart — blocked (sequence not satisfied)
        ("checkpoint_and_restart", "Degraded",  0.85, False),
        ("checkpoint_and_restart", "Healthy",   0.85, False),
        ("checkpoint_and_restart", "Recovered", 0.85, False),
        # checkpoint_and_restart — score below threshold
        ("checkpoint_and_restart", "Audited",   0.79, False),

        # escalate — valid states
        ("escalate",               "Degraded",  0.97, True),
        ("escalate",               "Audited",   0.96, True),
        ("escalate",               "Repairing", 0.95, True),
        # escalate — blocked (no fault present)
        ("escalate",               "Healthy",   0.97, False),
        ("escalate",               "Recovered", 0.97, False),
        # escalate — score below threshold
        ("escalate",               "Degraded",  0.94, False),

        # no_action — always allowed
        ("no_action",              "Healthy",   0.00, True),
        ("no_action",              "Repairing", 0.99, True),
    ]

    passed = 0
    for action, state, score, expect in cases:
        ok, reason = policy_check(action, state, score)
        status = "PASS" if ok == expect else "FAIL"
        if status == "FAIL":
            print(f"  {status}  {action}/{state}/{score} → got={ok}, want={expect} | {reason}")
        else:
            print(f"  {status}  {action}/{state}/{score} → {'allowed' if ok else 'blocked'}")
        assert ok == expect, f"Assertion failed: {action}/{state}/{score}"
        passed += 1

    # suppression_rate helper
    fake_traces = [
        {"wasm_blocked": True},
        {"wasm_blocked": False},
        {"wasm_blocked": True},
        {},
    ]
    rate = suppression_rate(fake_traces)
    assert rate == 0.5, f"Expected 0.5, got {rate}"
    print(f"\n  suppression_rate test: {rate} ✓")

    print(f"\nAll {passed} WASM policy checks passed.")
