# actions.py — F6: Stateful Action Executor
#
# Executes remediation decisions from stateful/reconcile.py.
# Every action carries a Reversibility tag (see REVERSIBILITY dict).
#
#   reversible   — safe to execute automatically; effect can be undone
#   conditional  — requires confidence threshold AND (production) human confirmation
#   irreversible — ALWAYS blocked by the reversibility gate in reconcile.py;
#                  never dispatched to execute()
#
# Action catalogue:
#   no_action              (reversible)   — no-op; confidence below threshold
#   flush_io_queue         (reversible)   — flush stalled I/O queues; first-line remedy
#   checkpoint_and_restart (reversible)   — CRIU checkpoint then docker restart
#   escalate               (conditional)  — page on-call; suppressed in shadow mode
#   volume_delete          (irreversible) — BLOCKED by gate; never reaches execute()

import subprocess
import time

# ── WASM policy sandbox (optional — falls back gracefully if wasmtime missing) ─
try:
    from wasm_executor import policy_check as _wasm_policy_check
    _WASM_AVAILABLE = True
except ImportError:
    import warnings as _warnings
    _WASM_AVAILABLE = False
    _warnings.warn(
        "[WASM-FALLBACK] wasmtime is not installed. "
        "Using Python policy fallback — FSM constraints are still enforced. "
        "Install wasmtime (`pip install wasmtime`) for full sandbox isolation.",
        RuntimeWarning,
        stacklevel=2,
    )

    def _wasm_policy_check(action, fsm_state, score):  # noqa: E302
        """
        Python-level policy fallback mirroring the WAT module logic exactly.
        Same FSM state checks and score thresholds as stateful/wasm/*.wat.
        FSM encoding: Healthy=0  Degraded=1  Audited=2  Repairing=3  Recovered=4
        """
        _FSM_CODE = {
            "Healthy": 0, "Degraded": 1, "Audited": 2,
            "Repairing": 3, "Recovered": 4,
        }
        fsm_code = _FSM_CODE.get(fsm_state)
        if fsm_code is None:
            return False, f"Python fallback: unknown FSM state {fsm_state!r}"

        if action == "no_action":
            return True, "Python fallback: no_action always allowed"

        if action == "flush_io_queue":
            if score < 0.50:
                return False, f"Python fallback: flush_io_queue blocked — score {score:.3f} < 0.50"
            if fsm_code in (1, 2):   # Degraded or Audited
                return True, "Python fallback: policy satisfied"
            return False, (
                f"Python fallback: flush_io_queue blocked — "
                f"FSM={fsm_state!r} not in (Degraded, Audited)"
            )

        if action == "checkpoint_and_restart":
            if score < 0.80:
                return False, (
                    f"Python fallback: checkpoint_and_restart blocked — "
                    f"score {score:.3f} < 0.80"
                )
            if fsm_code in (2, 3):   # Audited or Repairing
                return True, "Python fallback: policy satisfied"
            return False, (
                f"Python fallback: checkpoint_and_restart blocked — "
                f"FSM={fsm_state!r} not in (Audited, Repairing)"
            )

        if action == "escalate":
            if score < 0.95:
                return False, f"Python fallback: escalate blocked — score {score:.3f} < 0.95"
            if fsm_code in (1, 2, 3):   # Degraded, Audited, or Repairing
                return True, "Python fallback: policy satisfied"
            return False, (
                f"Python fallback: escalate blocked — "
                f"FSM={fsm_state!r} not in (Degraded, Audited, Repairing)"
            )

        # Unknown action — allow (mirrors no_action.wat behaviour for unregistered actions)
        return True, f"Python fallback: unknown action {action!r} — defaulting to allow"

# ── Reversibility catalogue — imported by reconcile.py for gate enforcement ───
REVERSIBILITY: dict = {
    "no_action":              "reversible",
    "flush_io_queue":         "reversible",
    "checkpoint_and_restart": "reversible",
    "escalate":               "conditional",
    "volume_delete":          "irreversible",   # gate in reconcile.py always blocks this
}

IRREVERSIBLE_ACTIONS = {k for k, v in REVERSIBILITY.items() if v == "irreversible"}


def _result(container: str, action: str, success: bool,
            stdout: str = "", stderr: str = "") -> dict:
    return {
        "container":     container,
        "action":        action,
        "success":       success,
        "stdout":        stdout,
        "stderr":        stderr,
        "timestamp":     time.time(),
        "mode":          "real",
        "reversibility": REVERSIBILITY.get(action, "unknown"),
    }


def no_action(container: str) -> dict:
    """No-op — confidence below threshold."""
    print(f"[actions-f] no_action: {container}")
    return _result(container, "no_action", True)


def flush_io_queue(container: str) -> dict:
    """
    Flush stalled I/O queues for the container.
    In a real environment this would trigger a blkio cgroup reset or
    send SIGSTOP/SIGCONT to unblock waiting syscalls.
    Simulated here.
    """
    print(f"[actions-f] flush_io_queue: {container}")
    return _result(
        container, "flush_io_queue", True,
        stdout=f"SIMULATED: I/O queue flushed for {container}",
    )


def checkpoint_and_restart(container: str) -> dict:
    """
    Create a container checkpoint (CRIU), then issue docker restart.
    Checkpoint step is simulated (CRIU requires --privileged).
    Restart step attempts real docker call and degrades gracefully if unavailable.
    """
    print(f"[actions-f] checkpoint_and_restart: {container}")
    ckpt_msg = f"SIMULATED: CRIU checkpoint created for {container}"
    try:
        result = subprocess.run(
            ["docker", "restart", container],
            capture_output=True, text=True, timeout=30,
        )
        success = result.returncode == 0
        return _result(
            container, "checkpoint_and_restart", success,
            stdout=f"{ckpt_msg} | docker stdout: {result.stdout.strip()}",
            stderr=result.stderr.strip(),
        )
    except FileNotFoundError:
        return _result(container, "checkpoint_and_restart", False,
                       stdout=ckpt_msg,
                       stderr="docker not found; checkpoint was simulated only")
    except subprocess.TimeoutExpired:
        return _result(container, "checkpoint_and_restart", False,
                       stdout=ckpt_msg,
                       stderr="docker restart timed out")


def escalate(container: str) -> dict:
    """
    Escalate to on-call operator.
    Conditional: only dispatched when confidence >= ESCALATE_THRESHOLD
    and the reversibility gate permits it.
    In production this would page PagerDuty / OpsGenie.
    """
    print(f"[actions-f] ESCALATE: {container}")
    return _result(
        container, "escalate", True,
        stdout=f"SIMULATED: on-call alert raised for {container} — manual intervention required",
    )


def execute(decision: dict) -> dict:
    """
    Dispatch a stateful decision dict to the correct action function.
    Shadow-mode decisions are logged but never executed.

    For real-mode decisions, the WASM sandbox policy_check() is called first.
    If the sandbox rejects the action (FSM-sequence violation or score below
    threshold), execution is suppressed and wasm_blocked=True is recorded.
    This is the second, independent gate described in the research paper.
    """
    container = decision["container"]
    action    = decision.get("action", "no_action")
    mode      = decision.get("mode", "real")

    if mode == "shadow":
        print(f"[actions-f][SHADOW] would execute '{action}' on {container} — skipping")
        return {
            "container":     container,
            "action":        action,
            "success":       None,
            "stdout":        f"SHADOW: would run {action}",
            "stderr":        "",
            "timestamp":     time.time(),
            "mode":          "shadow",
            "reversibility": REVERSIBILITY.get(action, "unknown"),
            "wasm_blocked":  False,
            "wasm_reason":   None,
        }

    # ── WASM sandbox check (real mode only) ───────────────────────────────────
    # Use fsm_state_after (state after Python-level FSM transitions) so the
    # WASM module sees the same state the Python gate used for its decision.
    fsm_state = decision.get("fsm_state_after", decision.get("fsm_state", "Healthy"))
    score     = float(decision.get("score", 0.0))
    wasm_ok, wasm_reason = _wasm_policy_check(action, fsm_state, score)

    if not wasm_ok:
        print(f"[actions-f][WASM-BLOCKED] {container}: {wasm_reason}")
        result = _result(container, "no_action", False, stderr=wasm_reason)
        result["mode"]         = "real"
        result["wasm_blocked"] = True
        result["wasm_reason"]  = wasm_reason
        return result

    dispatch = {
        "no_action":              no_action,
        "flush_io_queue":         flush_io_queue,
        "checkpoint_and_restart": checkpoint_and_restart,
        "escalate":               escalate,
    }
    fn = dispatch.get(action, no_action)
    result = fn(container)
    result["mode"]         = "real"
    result["wasm_blocked"] = False
    result["wasm_reason"]  = wasm_reason   # "WASM sandbox: policy satisfied"
    return result


# --- Self-test ---
if __name__ == "__main__":
    # Decision dicts must include fsm_state_after so the WASM sandbox
    # receives the correct post-transition FSM state (as reconcile.py provides).

    # no_action — always allowed (any FSM state)
    d = execute({"container": "postgres", "action": "no_action",
                 "mode": "real", "fsm_state_after": "Healthy", "score": 0.0})
    assert d["success"] is True and d["action"] == "no_action", d
    assert d["wasm_blocked"] is False, d

    # flush_io_queue — allowed from Degraded with score >= 0.50
    d = execute({"container": "postgres", "action": "flush_io_queue",
                 "mode": "real", "fsm_state_after": "Degraded", "score": 0.65})
    assert d["success"] is True and "SIMULATED" in d["stdout"], d
    assert d["wasm_blocked"] is False, d

    # flush_io_queue — WASM blocks it from Healthy (no degradation present)
    d = execute({"container": "postgres", "action": "flush_io_queue",
                 "mode": "real", "fsm_state_after": "Healthy", "score": 0.65})
    assert d["wasm_blocked"] is True, d
    print(f"  flush/Healthy: WASM-blocked as expected ✓")

    # escalate — allowed from Degraded with score >= 0.95
    d = execute({"container": "etcd", "action": "escalate",
                 "mode": "real", "fsm_state_after": "Degraded", "score": 0.97})
    assert d["success"] is True and "SIMULATED" in d["stdout"], d
    assert d["wasm_blocked"] is False, d

    # shadow — must not execute; WASM check is skipped in shadow mode
    d = execute({"container": "postgres", "action": "checkpoint_and_restart",
                 "mode": "shadow", "fsm_state_after": "Audited", "score": 0.85})
    assert d["mode"] == "shadow" and d["success"] is None, d
    assert "SHADOW" in d["stdout"], d

    # checkpoint_and_restart — allowed from Audited (docker may not be present)
    d = execute({"container": "nonexistent_uc_f_test", "action": "checkpoint_and_restart",
                 "mode": "real", "fsm_state_after": "Audited", "score": 0.85})
    assert d["action"] == "checkpoint_and_restart", d
    assert d["wasm_blocked"] is False, d
    print(f"  checkpoint_and_restart: success={d['success']} stderr={d['stderr']!r}")

    # checkpoint_and_restart — WASM blocks it from Degraded (must be Audited first)
    d = execute({"container": "postgres", "action": "checkpoint_and_restart",
                 "mode": "real", "fsm_state_after": "Degraded", "score": 0.85})
    assert d["wasm_blocked"] is True, d
    print(f"  checkpoint/Degraded: WASM-blocked as expected ✓")

    # Reversibility catalogue sanity
    assert REVERSIBILITY["flush_io_queue"]         == "reversible"
    assert REVERSIBILITY["escalate"]               == "conditional"
    assert REVERSIBILITY["volume_delete"]          == "irreversible"
    assert "volume_delete" in IRREVERSIBLE_ACTIONS

    print("\nAll self-tests passed.")
