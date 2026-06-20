# actions.py — S6: Action Executor
# Executes decisions produced by reconcile.py.
# restart   → runs `docker restart <container>`
# reschedule → simulated (log-only, no actual k8s call)
# no_action  → no-op

import subprocess
import time


REVERSIBILITY: dict = {
    "no_action":  "reversible",
    "restart":    "reversible",
    "reschedule": "reversible",
}


def restart(container: str) -> dict:
    """
    Run `docker restart <container>`.
    Returns a result dict with success/failure info.
    """
    print(f"[actions] restarting container: {container}", flush=True)
    try:
        result = subprocess.run(
            ["docker", "restart", container],
            capture_output=True, text=True, timeout=30
        )
        success = result.returncode == 0
        return {
            "container":     container,
            "action":        "restart",
            "success":       success,
            "stdout":        result.stdout.strip(),
            "stderr":        result.stderr.strip(),
            "timestamp":     time.time(),
            "reversibility": "reversible",
        }
    except FileNotFoundError:
        return {
            "container":     container,
            "action":        "restart",
            "success":       False,
            "stdout":        "",
            "stderr":        "docker not found",
            "timestamp":     time.time(),
            "reversibility": "reversible",
        }
    except subprocess.TimeoutExpired:
        return {
            "container":     container,
            "action":        "restart",
            "success":       False,
            "stdout":        "",
            "stderr":        "docker restart timed out",
            "timestamp":     time.time(),
            "reversibility": "reversible",
        }


def reschedule(container: str) -> dict:
    """
    Simulated reschedule — logs intent only, no actual k8s call.
    """
    print(f"[actions] rescheduling container: {container}", flush=True)
    return {
        "container":     container,
        "action":        "reschedule",
        "success":       True,
        "stdout":        f"Scheduler: reschedule intent logged for {container} — no live orchestrator",
        "stderr":        "",
        "timestamp":     time.time(),
        "reversibility": "reversible",
    }


def no_action(container: str) -> dict:
    """No-op — score was below threshold."""
    print(f"[actions] no_action for container: {container}", flush=True)
    return {
        "container":     container,
        "action":        "no_action",
        "success":       True,
        "stdout":        "",
        "stderr":        "",
        "timestamp":     time.time(),
        "reversibility": "reversible",
    }


def execute(decision: dict) -> dict:
    """
    Dispatch a decision dict (from reconcile.py) to the correct action.
    Shadow-mode decisions are logged but not executed.
    """
    container = decision["container"]
    action = decision.get("action", "no_action")
    mode = decision.get("mode", "real")

    if mode == "shadow":
        print(f"[actions][SHADOW] would execute '{action}' on {container} — skipping", flush=True)
        return {
            "container":     container,
            "action":        action,
            "success":       None,
            "stdout":        f"SHADOW: would run {action}",
            "stderr":        "",
            "timestamp":     time.time(),
            "mode":          "shadow",
            "reversibility": REVERSIBILITY.get(action, "reversible"),
        }

    if action == "restart":
        result = restart(container)
    elif action == "reschedule":
        result = reschedule(container)
    else:
        result = no_action(container)

    result["mode"] = "real"
    return result


# --- Self-test ---
if __name__ == "__main__":
    # Test no_action
    d = execute({"container": "myapp", "action": "no_action", "mode": "real"})
    assert d["action"] == "no_action" and d["success"] is True, d

    # Test reschedule
    d = execute({"container": "myapp", "action": "reschedule", "mode": "real"})
    assert d["action"] == "reschedule" and d["success"] is True, d
    assert d["stdout"] != "", d

    # Test shadow — should not actually execute
    d = execute({"container": "myapp", "action": "restart", "mode": "shadow"})
    assert d["mode"] == "shadow" and d["success"] is None, d
    assert "SHADOW" in d["stdout"], d

    # Test restart (docker may not exist; we check graceful failure)
    d = execute({"container": "nonexistent_test_container_uc", "action": "restart", "mode": "real"})
    assert d["action"] == "restart", d
    # success may be False if docker isn't running — that's fine
    print(f"  restart result: success={d['success']} stderr={d['stderr']!r}")

    print("\nAll self-tests passed.")
