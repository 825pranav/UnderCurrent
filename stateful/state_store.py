# state_store.py — F3: Stateful Failure State Tracker
#
# Tracks per-container I/O and filesystem failure events within a sliding time window.
# Interface is a strict superset of stateless/state_store.py:
#   record(), get_failures(), get_failure_count(), get_all_containers(), summary()
#   are identical in signature — stateless consumers can swap this in safely.
#   Stateful-only accessors (get_io_latency_stats, is_volume_mounted,
#   get_vfs_error_count) are purely additive.
#
# Supported event types:
#   blk_io_latency        — block I/O completed; carries latency_us field
#   vfs_write_error       — VFS write syscall returned an error
#   vfs_read_error        — VFS read syscall returned an error
#   volume_mount_lost     — volume mount disappeared at runtime
#   volume_mount_restored — volume mount came back

import time
from collections import defaultdict

# High-latency threshold used by get_io_latency_stats
IO_HIGH_LATENCY_US = 10_000   # 10 ms


class StatefulStateStore:
    """
    Sliding-window event store for stateful (Type F) workloads.

    Event dict format (superset of stateless event format):
      {
        "container":    str,    # workload name
        "pid":          int,
        "event":        str,    # one of the types listed above
        "time":         float,  # unix timestamp
        "latency_us":   int,    # blk_io_latency only — microseconds
        "volume_path":  str,    # volume_* events only
        "node_type":    "F",    # always present on stateful events
      }
    """

    def __init__(self, window_seconds: int = 60):
        self.window = window_seconds
        # { container: [event_dict, ...] }
        self._events: dict = defaultdict(list)
        # { container: bool } — last known mount status; default True (assumed mounted)
        self._mount_status: dict = {}

    # ── Core interface (identical signatures to stateless/state_store.py) ──────

    def record(self, event: dict):
        """Store an event from event_listener.py."""
        container = event.get("container", "unknown")
        self._events[container].append(event)
        # Keep mount status cache in sync for instant lookup
        ev = event.get("event", "")
        if ev == "volume_mount_lost":
            self._mount_status[container] = False
        elif ev == "volume_mount_restored":
            self._mount_status[container] = True

    def _prune(self, container: str):
        """Remove events outside the sliding time window."""
        cutoff = time.time() - self.window
        self._events[container] = [
            e for e in self._events[container] if e.get("time", 0) >= cutoff
        ]

    def get_failures(self, container: str) -> list:
        """Return all recent events for a container within the window."""
        self._prune(container)
        return self._events[container]

    def get_failure_count(self, container: str) -> int:
        """Count recent events for a container."""
        return len(self.get_failures(container))

    def get_all_containers(self) -> list:
        """List all containers that have recorded events."""
        return list(self._events.keys())

    def summary(self) -> dict:
        """Snapshot of all containers and their recent event counts."""
        return {c: self.get_failure_count(c) for c in self.get_all_containers()}

    # ── Stateful-only accessors ────────────────────────────────────────────────

    def get_io_latency_stats(self, container: str) -> dict:
        """
        I/O latency statistics for blk_io_latency events within the window.
        Returns:
          count      — total blk_io events
          mean_us    — mean latency across all blk_io events
          max_us     — peak latency
          high_count — events exceeding IO_HIGH_LATENCY_US threshold
        """
        events = [
            e for e in self.get_failures(container)
            if e.get("event") == "blk_io_latency"
        ]
        if not events:
            return {"count": 0, "mean_us": 0.0, "max_us": 0, "high_count": 0}
        latencies = [e.get("latency_us", 0) for e in events]
        return {
            "count":      len(latencies),
            "mean_us":    round(sum(latencies) / len(latencies), 1),
            "max_us":     max(latencies),
            "high_count": sum(1 for lat in latencies if lat >= IO_HIGH_LATENCY_US),
        }

    def is_volume_mounted(self, container: str) -> bool:
        """
        Current volume mount status.
        Defaults to True if no volume events have been recorded yet.
        """
        return self._mount_status.get(container, True)

    def get_vfs_error_count(self, container: str) -> int:
        """Count vfs_write_error and vfs_read_error events within the window."""
        return sum(
            1 for e in self.get_failures(container)
            if e.get("event") in ("vfs_write_error", "vfs_read_error")
        )


# --- Quick self-test ---
if __name__ == "__main__":
    store = StatefulStateStore(window_seconds=10)
    now = time.time()

    store.record({"container": "postgres", "pid": 101, "event": "blk_io_latency",
                  "latency_us": 500,    "time": now, "node_type": "F"})
    store.record({"container": "postgres", "pid": 102, "event": "blk_io_latency",
                  "latency_us": 15_000, "time": now, "node_type": "F"})
    store.record({"container": "postgres", "pid": 103, "event": "vfs_write_error",
                  "time": now, "node_type": "F"})
    store.record({"container": "mysql", "pid": 201, "event": "volume_mount_lost",
                  "volume_path": "/data", "time": now, "node_type": "F"})
    # Old event — should be pruned (30s ago, window is 10s)
    store.record({"container": "postgres", "pid": 999, "event": "blk_io_latency",
                  "latency_us": 20_000, "time": now - 30, "node_type": "F"})

    print("Summary:", store.summary())
    assert store.get_failure_count("postgres") == 3, store.get_failure_count("postgres")

    stats = store.get_io_latency_stats("postgres")
    print("Postgres I/O stats:", stats)
    assert stats["high_count"] == 1    # only 15_000 µs event qualifies

    assert store.is_volume_mounted("mysql") is False
    assert store.is_volume_mounted("postgres") is True   # no mount events → defaults True
    assert store.get_vfs_error_count("postgres") == 1

    print("\nAll self-tests passed.")
