# reconcile.py — S5: Decision Engine
# Runs a diagnostic DAG over confidence scores and recommends actions.
# Supports a shadow (dry-run) controller that logs what it *would* do.

import time
from confidence import score_all

# --- Tunable thresholds ---
RESTART_THRESHOLD = 0.80        # score >= this → recommend restart
RESCHEDULE_THRESHOLD = 0.95     # score >= this → recommend reschedule instead


def _decide(container: str, score: float) -> dict:
    """
    Diagnostic DAG:
      score < RESTART_THRESHOLD      → no_action
      RESTART_THRESHOLD <= score < RESCHEDULE_THRESHOLD → restart
      score >= RESCHEDULE_THRESHOLD  → reschedule
    Returns a decision dict.
    """
    if score >= RESCHEDULE_THRESHOLD:
        action = "reschedule"
        reason = f"score {score} >= reschedule threshold {RESCHEDULE_THRESHOLD}"
    elif score >= RESTART_THRESHOLD:
        action = "restart"
        reason = f"score {score} >= restart threshold {RESTART_THRESHOLD}"
    else:
        action = "no_action"
        reason = f"score {score} below restart threshold {RESTART_THRESHOLD}"

    return {
        "container": container,
        "score": score,
        "action": action,
        "reason": reason,
        "timestamp": time.time(),
    }


def reconcile(state_store, shadow: bool = False) -> list:
    """
    Run the decision engine over all tracked containers.

    shadow=False → real path (decisions are acted upon by actions.py)
    shadow=True  → dry-run path (decisions are logged but not executed)

    Returns list of decision dicts with 'mode' field set.
    """
    scores = score_all(state_store)
    decisions = []

    for container, score in scores.items():
        decision = _decide(container, score)
        decision["mode"] = "shadow" if shadow else "real"
        decisions.append(decision)

        tag = "[SHADOW]" if shadow else "[REAL]"
        print(
            f"{tag} container={container} score={score} "
            f"→ {decision['action']}  ({decision['reason']})"
        )

    return decisions


def reconcile_both(state_store) -> dict:
    """
    Run both real and shadow paths and return both sets of decisions.
    Useful for comparing what the system does vs. what it would have done.
    """
    print("--- Real path ---")
    real = reconcile(state_store, shadow=False)
    print("--- Shadow path ---")
    shadow = reconcile(state_store, shadow=True)
    return {"real": real, "shadow": shadow}


# --- Self-test ---
if __name__ == "__main__":
    from state_store import StateStore

    store = StateStore(window_seconds=60)
    now = time.time()

    # nginx: process_exit → score 0.95 → reschedule
    store.record({"container": "nginx",  "pid": 1, "event": "process_exit",    "time": now})
    # pyapp: 3 tcp_connect_fail → score 0.54 → no_action
    for i in range(3):
        store.record({"container": "pyapp",  "pid": 10+i, "event": "tcp_connect_fail", "time": now})
    # redis: 5 tcp_connect_fail → score 0.90 → restart
    for i in range(5):
        store.record({"container": "redis",  "pid": 20+i, "event": "tcp_connect_fail", "time": now})

    result = reconcile_both(store)

    # Validate real path
    real_by_name = {d["container"]: d for d in result["real"]}
    assert real_by_name["nginx"]["action"] == "reschedule", real_by_name["nginx"]
    assert real_by_name["pyapp"]["action"] == "no_action",  real_by_name["pyapp"]
    assert real_by_name["redis"]["action"] == "restart",    real_by_name["redis"]

    # Shadow path should match real path decisions
    shadow_by_name = {d["container"]: d for d in result["shadow"]}
    for c in real_by_name:
        assert real_by_name[c]["action"] == shadow_by_name[c]["action"], \
            f"Mismatch for {c}: real={real_by_name[c]['action']} shadow={shadow_by_name[c]['action']}"
        assert shadow_by_name[c]["mode"] == "shadow"
        assert real_by_name[c]["mode"] == "real"

    print("\nAll self-tests passed.")
