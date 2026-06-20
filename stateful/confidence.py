# confidence.py — F4: Stateful Risk Confidence Scorer
#
# Computes a risk score (0.0–1.0) per container from I/O and filesystem signals.
# Used by stateful/reconcile.py to drive FSM-gated remediation decisions.
#
# Three independent scorers (same combination rule as stateless/confidence.py — max):
#   1. blk_io_latency degradation  → scales with count of high-latency events
#   2. VFS error frequency         → scales with error count in window
#   3. Volume mount loss           → immediate high score on any unresolved mount loss
#
# Thresholds are tunable; defaults are calibrated for a 60s sliding window.

import time

# ── Tunable thresholds ────────────────────────────────────────────────────────
IO_HIGH_LATENCY_US    = 10_000   # 10 ms — event counted as "high latency" above this
IO_LATENCY_SCALE_RATE = 5        # high-latency events needed to reach ceiling
IO_LATENCY_SCORE_MAX  = 0.85     # ceiling for pure I/O latency degradation

VFS_ERROR_SCALE_RATE  = 3        # VFS errors in window needed to reach ceiling
VFS_ERROR_SCORE_MAX   = 0.88     # ceiling for VFS error accumulation

MOUNT_LOSS_SCORE      = 0.97     # score for any unresolved volume_mount_lost in window


def score_io_latency(failures: list) -> float:
    """
    I/O latency confidence scales with the number of high-latency blk_io events.
    0 high-latency events → 0.0
    IO_LATENCY_SCALE_RATE or more → IO_LATENCY_SCORE_MAX
    """
    high = [
        e for e in failures
        if e.get("event") == "blk_io_latency"
        and e.get("latency_us", 0) >= IO_HIGH_LATENCY_US
    ]
    count = len(high)
    if count == 0:
        return 0.0
    ratio = min(count / IO_LATENCY_SCALE_RATE, 1.0)
    return round(ratio * IO_LATENCY_SCORE_MAX, 4)


def score_vfs_errors(failures: list) -> float:
    """
    VFS error confidence scales with error frequency in the window.
    0 errors → 0.0
    VFS_ERROR_SCALE_RATE or more → VFS_ERROR_SCORE_MAX
    """
    vfs = [
        e for e in failures
        if e.get("event") in ("vfs_write_error", "vfs_read_error")
    ]
    count = len(vfs)
    if count == 0:
        return 0.0
    ratio = min(count / VFS_ERROR_SCALE_RATE, 1.0)
    return round(ratio * VFS_ERROR_SCORE_MAX, 4)


def score_volume_mount_loss(failures: list) -> float:
    """
    Walk events in time order to determine whether a mount loss is currently unresolved.
    Any unresolved volume_mount_lost in the window → MOUNT_LOSS_SCORE.
    A subsequent volume_mount_restored resolves it.
    """
    mount_lost = False
    for e in sorted(failures, key=lambda x: x.get("time", 0)):
        ev = e.get("event", "")
        if ev == "volume_mount_lost":
            mount_lost = True
        elif ev == "volume_mount_restored":
            mount_lost = False
    return MOUNT_LOSS_SCORE if mount_lost else 0.0


def compute_confidence(container: str, state_store) -> float:
    """
    Combined risk confidence score for a stateful container.
    Returns max of all signal scores (same combination rule as stateless/confidence.py).
    """
    failures = state_store.get_failures(container)
    if not failures:
        return 0.0
    scores = [
        score_io_latency(failures),
        score_vfs_errors(failures),
        score_volume_mount_loss(failures),
    ]
    return round(max(scores), 4)


def score_all(state_store) -> dict:
    """
    Confidence scores for every container currently tracked.
    Returns { container_name: score }
    """
    return {
        c: compute_confidence(c, state_store)
        for c in state_store.get_all_containers()
    }


# --- Self-test ---
if __name__ == "__main__":
    from state_store import StatefulStateStore

    store = StatefulStateStore(window_seconds=60)
    now = time.time()

    # postgres: 5 high-latency blk_io events → should hit IO ceiling 0.85
    for i in range(5):
        store.record({"container": "postgres", "pid": 100 + i,
                      "event": "blk_io_latency", "latency_us": 15_000,
                      "time": now, "node_type": "F"})

    # mysql: 2 VFS errors → 2/3 * 0.88 ≈ 0.5867
    for i in range(2):
        store.record({"container": "mysql", "pid": 200 + i,
                      "event": "vfs_write_error", "time": now, "node_type": "F"})

    # redis: volume mount lost → 0.97
    store.record({"container": "redis", "pid": 301, "event": "volume_mount_lost",
                  "volume_path": "/data", "time": now, "node_type": "F"})

    # redis (second store): mount lost then restored → score drops to 0.0
    store2 = StatefulStateStore(window_seconds=60)
    store2.record({"container": "redis", "pid": 401, "event": "volume_mount_lost",
                   "volume_path": "/logs", "time": now - 5, "node_type": "F"})
    store2.record({"container": "redis", "pid": 402, "event": "volume_mount_restored",
                   "volume_path": "/logs", "time": now, "node_type": "F"})

    # mysql (second store): only normal-latency events → 0.0
    store3 = StatefulStateStore(window_seconds=60)
    for i in range(3):
        store3.record({"container": "mysql", "pid": 500 + i,
                       "event": "blk_io_latency", "latency_us": 500,
                       "time": now, "node_type": "F"})

    scores = score_all(store)
    for c, s in sorted(scores.items()):
        print(f"  {c:<16} → confidence: {s}")

    assert scores["postgres"] == IO_LATENCY_SCORE_MAX,                  f"Got {scores['postgres']}"
    assert scores["mysql"]    == round(2 / 3 * VFS_ERROR_SCORE_MAX, 4), f"Got {scores['mysql']}"
    assert scores["redis"]    == MOUNT_LOSS_SCORE,                      f"Got {scores['redis']}"

    scores2 = score_all(store2)
    assert scores2["redis"] == 0.0, f"mount-restored → expected 0.0, got {scores2['redis']}"

    scores3 = score_all(store3)
    assert scores3["mysql"] == 0.0, f"normal-latency-only → expected 0.0, got {scores3['mysql']}"

    print("\nAll self-tests passed.")
