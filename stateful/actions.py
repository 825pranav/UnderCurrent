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
        }

    dispatch = {
        "no_action":              no_action,
        "flush_io_queue":         flush_io_queue,
        "checkpoint_and_restart": checkpoint_and_restart,
        "escalate":               escalate,
    }
    fn = dispatch.get(action, no_action)
    result = fn(container)
    result["mode"] = "real"
    return result


# --- Self-test ---
if __name__ == "__main__":
    # no_action
    d = execute({"container": "postgres", "action": "no_action", "mode": "real"})
    assert d["success"] is True and d["action"] == "no_action", d

    # flush_io_queue
    d = execute({"container": "postgres", "action": "flush_io_queue", "mode": "real"})
    assert d["success"] is True and "SIMULATED" in d["stdout"], d

    # escalate
    d = execute({"container": "etcd", "action": "escalate", "mode": "real"})
    assert d["success"] is True and "SIMULATED" in d["stdout"], d

    # shadow — must not execute
    d = execute({"container": "postgres", "action": "checkpoint_and_restart", "mode": "shadow"})
    assert d["mode"] == "shadow" and d["success"] is None, d
    assert "SHADOW" in d["stdout"], d

    # checkpoint_and_restart (docker may not be present — graceful degradation)
    d = execute({"container": "nonexistent_uc_f_test", "action": "checkpoint_and_restart", "mode": "real"})
    assert d["action"] == "checkpoint_and_restart", d
    print(f"  checkpoint_and_restart: success={d['success']} stderr={d['stderr']!r}")

    # Reversibility catalogue sanity
    assert REVERSIBILITY["flush_io_queue"]         == "reversible"
    assert REVERSIBILITY["escalate"]               == "conditional"
    assert REVERSIBILITY["volume_delete"]          == "irreversible"
    assert "volume_delete" in IRREVERSIBLE_ACTIONS

    print("\nAll self-tests passed.")
