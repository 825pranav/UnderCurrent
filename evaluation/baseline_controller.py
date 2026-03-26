# evaluation/baseline_controller.py — UnderCurrent Naive Baseline Controller
#
# A rule-based threshold controller with the same external decision interface
# as the real stateless/stateful reconcile functions, but without confidence
# scoring, FSM state tracking, or sliding-window history analysis.
#
# Purpose: provides a comparison baseline to quantify the performance lift
# delivered by the real controllers' confidence scoring and FSM gating.
#
# Usage (standalone):
#   python evaluation/baseline_controller.py
#
# Usage (as library):
#   from evaluation.baseline_controller import BaselineController

import sys
import os
import time

# ── sys.path: ensure sibling packages are importable ──────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'stateless'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'stateful'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))


class BaselineController:
    """
    Naive event-count threshold controller.

    No confidence scoring, no FSM, no state history — just raw event counts
    over the full list of events supplied at decision time.

    Used as the comparison baseline in the evaluation: any performance gap
    between this controller and the real stateless/stateful controllers
    quantifies the value of confidence scoring and FSM gating.

    All decisions are returned in a format that is backward-compatible with
    the decision dicts produced by stateless/reconcile.py and
    stateful/reconcile.py so that compute_metrics.py can process them uniformly.

    The 'score' field is set to -1.0 to indicate that no scoring was performed.
    The 'mode' field is always 'real' (the baseline has no shadow path).
    """

    # Raw event-count thresholds (no scaling, no scoring)
    _TCP_FAIL_THRESHOLD     = 5      # ≥ this many → restart
    _BLK_IO_HIGH_THRESHOLD  = 5      # ≥ this many high-latency → flush
    _VFS_ERR_THRESHOLD      = 3      # ≥ this many → flush
    _BLK_IO_LATENCY_CUTOFF  = 10_000 # µs — events above this are "high latency"

    def decide(self, events: list, container: str) -> dict:
        """
        Apply raw event-count rules to produce a remediation decision.

        Rules are checked in strict priority order; the first matching rule wins:
          1. Any process_exit event                           → "reschedule"
          2. volume_mount_lost (unresolved by restore)        → "escalate"
          3. ≥5 high-latency blk_io + ≥3 vfs errors          → "checkpoint_and_restart"
          4. ≥5 high-latency blk_io events                    → "flush_io_queue"
          5. ≥3 vfs_write_error or vfs_read_error events      → "flush_io_queue"
          6. ≥5 tcp_connect_fail events                       → "restart"
          7. None of the above                                → "no_action"

        Args:
            events:    list of event dicts for the container within the window.
            container: name of the container being evaluated.

        Returns:
            Decision dict with fields: container, score, action, reason, mode,
            kernel_signals, dag_pattern, decision_latency_ns.
        """
        t_start = time.perf_counter_ns()

        # Count each signal type
        process_exits   = [e for e in events if e.get("event") == "process_exit"]
        tcp_fails       = [e for e in events if e.get("event") == "tcp_connect_fail"]
        blk_high        = [
            e for e in events
            if e.get("event") == "blk_io_latency"
            and e.get("latency_us", 0) >= self._BLK_IO_LATENCY_CUTOFF
        ]
        vfs_errors      = [
            e for e in events
            if e.get("event") in ("vfs_write_error", "vfs_read_error")
        ]

        # Determine unresolved volume mount loss (walk events in time order)
        volume_lost = False
        for e in sorted(events, key=lambda x: x.get("time", 0)):
            ev = e.get("event", "")
            if ev == "volume_mount_lost":
                volume_lost = True
            elif ev == "volume_mount_restored":
                volume_lost = False

        # Build kernel_signals list (mirrors real controllers' format)
        signals = set()
        for e in events:
            ev = e.get("event", "")
            if ev == "process_exit":
                signals.add("process_exit")
            elif ev == "tcp_connect_fail":
                signals.add("tcp_connect_fail")
            elif ev == "blk_io_latency":
                if e.get("latency_us", 0) >= self._BLK_IO_LATENCY_CUTOFF:
                    signals.add("blk_io_latency_high")
                else:
                    signals.add("blk_io_latency_normal")
            elif ev == "vfs_write_error":
                signals.add("vfs_write_error")
            elif ev == "vfs_read_error":
                signals.add("vfs_read_error")
            elif ev == "volume_mount_lost":
                signals.add("volume_mount_lost")
            elif ev == "volume_mount_restored":
                signals.add("volume_mount_restored")

        # Priority-ordered rule evaluation
        if process_exits:
            action      = "reschedule"
            reason      = f"process_exit detected ({len(process_exits)} event(s))"
            dag_pattern = "process_failure"

        elif volume_lost:
            action      = "escalate"
            reason      = "unresolved volume_mount_lost"
            dag_pattern = "io_degradation_critical"

        elif len(blk_high) >= self._BLK_IO_HIGH_THRESHOLD and \
                len(vfs_errors) >= self._VFS_ERR_THRESHOLD:
            action      = "checkpoint_and_restart"
            reason      = (f"combined: {len(blk_high)} high-latency blk_io events + "
                           f"{len(vfs_errors)} vfs errors")
            dag_pattern = "io_degradation_major"

        elif len(blk_high) >= self._BLK_IO_HIGH_THRESHOLD:
            action      = "flush_io_queue"
            reason      = f"{len(blk_high)} high-latency blk_io events"
            dag_pattern = "io_degradation_minor"

        elif len(vfs_errors) >= self._VFS_ERR_THRESHOLD:
            action      = "flush_io_queue"
            reason      = f"{len(vfs_errors)} vfs error(s)"
            dag_pattern = "io_degradation_minor"

        elif len(tcp_fails) >= self._TCP_FAIL_THRESHOLD:
            action      = "restart"
            reason      = f"{len(tcp_fails)} tcp_connect_fail events"
            dag_pattern = "network_degraded"

        else:
            action      = "no_action"
            reason      = "no threshold crossed"
            dag_pattern = "nominal"

        t_end = time.perf_counter_ns()

        return {
            "container":          container,
            "score":              -1.0,          # no scoring in baseline
            "action":             action,
            "reason":             reason,
            "mode":               "real",
            "kernel_signals":     sorted(signals),
            "dag_pattern":        dag_pattern,
            "timestamp":          time.time(),
            "decision_latency_ns": t_end - t_start,
        }

    def decide_all(self, state_store) -> list:
        """
        Run decide() over every container currently tracked in a StateStore.

        Compatible with both stateless.state_store.StateStore and
        stateful.state_store.StatefulStateStore — any object that implements
        get_all_containers() and get_failures(container) with identical signatures.

        Args:
            state_store: a StateStore or StatefulStateStore instance.

        Returns:
            List of decision dicts (one per tracked container).
        """
        decisions = []
        for container in state_store.get_all_containers():
            events   = state_store.get_failures(container)
            decision = self.decide(events, container)
            decisions.append(decision)
        return decisions


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ctrl = BaselineController()
    now  = time.time()

    def _ev(event_type, container="test", pid=1, **kw):
        return {"container": container, "event": event_type,
                "pid": pid, "time": now, **kw}

    # Rule 1: process_exit → reschedule
    d = ctrl.decide([_ev("process_exit"), _ev("tcp_connect_fail")], "c1")
    assert d["action"] == "reschedule", d
    assert "process_exit" in d["kernel_signals"]
    print(f"  process_exit → {d['action']}  OK")

    # Rule 2: volume_mount_lost (unresolved) → escalate
    d = ctrl.decide([_ev("volume_mount_lost", volume_path="/data")], "c2")
    assert d["action"] == "escalate", d
    print(f"  volume_mount_lost → {d['action']}  OK")

    # Rule 2 resolved: mount lost then restored → falls through to no_action
    d = ctrl.decide([
        _ev("volume_mount_lost",    volume_path="/data"),
        _ev("volume_mount_restored", volume_path="/data"),
    ], "c2b")
    assert d["action"] == "no_action", d
    print(f"  volume_mount_lost+restored → {d['action']}  OK")

    # Rule 3: combined blk_io + vfs → checkpoint_and_restart
    blk_events = [_ev("blk_io_latency", latency_us=15000) for _ in range(5)]
    vfs_events = [_ev("vfs_write_error") for _ in range(3)]
    d = ctrl.decide(blk_events + vfs_events, "c3")
    assert d["action"] == "checkpoint_and_restart", d
    print(f"  blk_io×5 + vfs×3 → {d['action']}  OK")

    # Rule 4: high-latency blk_io alone → flush_io_queue
    d = ctrl.decide([_ev("blk_io_latency", latency_us=15000) for _ in range(5)], "c4")
    assert d["action"] == "flush_io_queue", d
    print(f"  blk_io×5 → {d['action']}  OK")

    # Rule 5: vfs errors alone → flush_io_queue
    d = ctrl.decide([_ev("vfs_write_error") for _ in range(3)], "c5")
    assert d["action"] == "flush_io_queue", d
    print(f"  vfs_errors×3 → {d['action']}  OK")

    # Rule 6: tcp_connect_fail × 5 → restart
    d = ctrl.decide([_ev("tcp_connect_fail") for _ in range(5)], "c6")
    assert d["action"] == "restart", d
    assert "decision_latency_ns" in d and isinstance(d["decision_latency_ns"], int)
    print(f"  tcp_fail×5 → {d['action']}  OK  latency={d['decision_latency_ns']}ns")

    # Rule 7: too few events → no_action
    d = ctrl.decide([_ev("tcp_connect_fail") for _ in range(3)], "c7")
    assert d["action"] == "no_action", d
    print(f"  tcp_fail×3 → {d['action']}  OK")

    # decide_all via a mock store
    class _MockStore:
        def get_all_containers(self):
            return ["alpha", "beta"]
        def get_failures(self, c):
            if c == "alpha":
                return [_ev("process_exit", container="alpha")]
            return []

    results = ctrl.decide_all(_MockStore())
    by_name = {d["container"]: d for d in results}
    assert by_name["alpha"]["action"] == "reschedule", by_name["alpha"]
    assert by_name["beta"]["action"]  == "no_action",  by_name["beta"]
    print("  decide_all() over mock store — OK")

    print("\nAll self-tests passed.")
