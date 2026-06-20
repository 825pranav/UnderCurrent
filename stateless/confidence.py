# confidence.py — S4: Risk Confidence Scorer
# Computes a risk score (0.0–1.0) per container based on failure history.
# Used by reconcile.py to decide whether to trigger an action.

import time

# --- Tunable thresholds ---
PROCESS_EXIT_BASE = 0.95        # base score for any process_exit event
TCP_CONNECT_SCALE_MAX = 0.90       # ceiling score for tcp_connect
TCP_CONNECT_SCALE_RATE = 5         # failures needed to reach ceiling


def score_process_exit(failures: list) -> float:
    """
    Any process_exit event immediately returns a high base confidence.
    Multiple exits don't push it above 1.0.
    """
    exit_events = [e for e in failures if e.get("event") == "process_exit"]
    if not exit_events:
        return 0.0
    return PROCESS_EXIT_BASE


def score_tcp_connect(failures: list) -> float:
    """
    tcp_connect confidence scales with frequency within the window.
    0 failures → 0.0
    TCP_CONNECT_SCALE_RATE or more failures → TCP_CONNECT_SCALE_MAX
    """
    tcp_events = [e for e in failures if e.get("event") == "tcp_connect"]
    count = len(tcp_events)
    if count == 0:
        return 0.0
    ratio = min(count / TCP_CONNECT_SCALE_RATE, 1.0)
    return round(ratio * TCP_CONNECT_SCALE_MAX, 4)


def compute_confidence(container: str, state_store) -> float:
    """
    Compute the combined risk confidence score for a container.
    Returns the max of all event-type scores (0.0–1.0).
    """
    failures = state_store.get_failures(container)
    if not failures:
        return 0.0

    scores = [
        score_process_exit(failures),
        score_tcp_connect(failures),
    ]
    return round(max(scores), 4)


def score_all(state_store) -> dict:
    """
    Return confidence scores for every container currently tracked.
    { container_name: score }
    """
    return {
        c: compute_confidence(c, state_store)
        for c in state_store.get_all_containers()
    }


# --- Self-test ---
if __name__ == "__main__":
    from state_store import StateStore

    store = StateStore(window_seconds=60)
    now = time.time()

    # nginx: has a process_exit → should be 0.95
    store.record({"container": "nginx", "pid": 101, "event": "process_exit", "time": now})

    # pyapp: 3 tcp_connect events → 3/5 * 0.90 = 0.54
    for i in range(3):
        store.record({"container": "pyapp", "pid": 200 + i, "event": "tcp_connect", "time": now})

    # redis: 5+ tcp_connect → should hit ceiling 0.90
    for i in range(6):
        store.record({"container": "redis", "pid": 300 + i, "event": "tcp_connect", "time": now})

    # clean: no events → 0.0
    store.record({"container": "clean", "pid": 400, "event": "some_other_event", "time": now})

    scores = score_all(store)
    for container, score in scores.items():
        print(f"  {container:<12} → confidence: {score}")

    assert scores["nginx"] == 0.95, f"Expected 0.95, got {scores['nginx']}"
    assert scores["pyapp"] == 0.54, f"Expected 0.54, got {scores['pyapp']}"
    assert scores["redis"] == TCP_CONNECT_SCALE_MAX, f"Expected {TCP_CONNECT_SCALE_MAX}, got {scores['redis']}"
    assert scores["clean"] == 0.0, f"Expected 0.0, got {scores['clean']}"

    print("\nAll self-tests passed.")
