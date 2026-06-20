# state_store.py — S3: Failure State Tracker
# Tracks per-container failure counts within a sliding time window.
# Used by confidence.py to score risk based on failure frequency.

import time
from collections import defaultdict


class StateStore:
    def __init__(self, window_seconds=60):
        """
        window_seconds: only count failures within this time window
        """
        self.window = window_seconds
        # { container_name: [ { "event": ..., "time": ..., "pid": ... }, ... ] }
        self._events = defaultdict(list)

    def record(self, event: dict):
        """Store an event from event_listener.py"""
        container = event.get("container", "unknown")
        self._events[container].append(event)

    def _prune(self, container: str):
        """Remove events outside the time window"""
        cutoff = time.time() - self.window
        self._events[container] = [
            e for e in self._events[container] if e.get("time", 0) >= cutoff
        ]

    def get_failures(self, container: str) -> list:
        """Get all recent failures for a container within the window"""
        self._prune(container)
        return self._events[container]

    def get_failure_count(self, container: str) -> int:
        """Count recent failures for a container"""
        return len(self.get_failures(container))

    def get_all_containers(self) -> list:
        """List all containers that have recorded events"""
        return list(self._events.keys())

    def summary(self) -> dict:
        """Snapshot of all containers and their recent failure counts"""
        return {
            c: self.get_failure_count(c) for c in self.get_all_containers()
        }


# --- Quick self-test ---
if __name__ == "__main__":
    store = StateStore(window_seconds=10)

    # Simulate events from event_listener.py
    store.record({"container": "nginx", "pid": 1001, "event": "process_exit", "time": time.time()})
    store.record({"container": "nginx", "pid": 1002, "event": "process_exit", "time": time.time()})
    store.record({"container": "pyapp", "pid": 2001, "event": "tcp_connect", "time": time.time()})

    # Old event (should be pruned)
    store.record({"container": "nginx", "pid": 999, "event": "process_exit", "time": time.time() - 30})

    print("Summary:", store.summary())
    print("Nginx failures:", store.get_failure_count("nginx"))
    print("Pyapp failures:", store.get_failure_count("pyapp"))

