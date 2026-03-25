# main.py — UnderCurrent Stateless Control Plane Orchestrator
#
# Wires the full pipeline:
#   EventListener → StateStore → Confidence → Reconcile → Actions → TraceLogger
#
# Modes:
#   --simulate   (default) Generate synthetic events; no BPF/root required
#   --real                 Use live eBPF EventListener (requires root + bcc)
#
# Usage:
#   python3 main.py                  # simulate mode
#   python3 main.py --real           # real eBPF mode (root required)
#   python3 main.py --interval 10    # reconcile every 10s (default: 5s)
#   python3 main.py --shadow         # shadow-only mode (no real actions)

import argparse
import json
import os
import random
import sys
import threading
import time

from state_store import StateStore
from reconcile import reconcile, reconcile_both
from actions import execute
from trace_logger import log_decision

# --- Config ---
WINDOW_SECONDS = 60
DIVERGENCE_LOG = os.path.join(os.path.dirname(__file__), "divergence_log.jsonl")
BANNER = """
╔══════════════════════════════════════════════╗
║        UnderCurrent  Control Plane           ║
║     Risk-Aware Autonomic Remediation         ║
╚══════════════════════════════════════════════╝
"""

# ---------------------------------------------------------------------------
# Simulated event source (no BPF required)
# ---------------------------------------------------------------------------
SIMULATED_CONTAINERS = ["nginx", "pyapp", "redis", "worker", "gateway"]
SIMULATED_EVENTS     = ["process_exit", "tcp_connect_fail", "tcp_connect_fail"]


def _simulate_events(store: StateStore, stop_event: threading.Event, rate: float = 1.5):
    """
    Background thread: emit synthetic events into the StateStore.
    rate: average seconds between events.
    """
    while not stop_event.is_set():
        event = {
            "container": random.choice(SIMULATED_CONTAINERS),
            "pid":        random.randint(1000, 99999),
            "event":      random.choice(SIMULATED_EVENTS),
            "time":       time.time(),
        }
        store.record(event)
        print(f"  [sim] {json.dumps(event)}", flush=True)
        time.sleep(random.uniform(rate * 0.5, rate * 1.5))


# ---------------------------------------------------------------------------
# Real eBPF event source
# ---------------------------------------------------------------------------
def _real_events(store: StateStore, stop_event: threading.Event):
    """
    Background thread: capture live kernel events via eBPF and feed StateStore.
    Requires root and the bcc library.
    """
    try:
        from bcc import BPF
        import ctypes
        from event_listener import EventListener
    except ImportError as e:
        print(f"[main] ERROR: cannot import bcc — {e}")
        print("[main] Run with --simulate or install bcc (requires root).")
        stop_event.set()
        return

    class _FeedingListener(EventListener):
        """Subclass that feeds events into StateStore instead of just printing."""
        def handle_event(self, cpu, data, size):
            import time as _time
            event_raw = self.b["events"].event(data)
            event_type = "process_exit" if event_raw.type == 0 else "tcp_connect_fail"
            record = {
                "container": event_raw.comm.decode("utf-8", errors="replace"),
                "pid":       event_raw.pid,
                "event":     event_type,
                "time":      _time.time(),
            }
            store.record(record)
            print(f"  [ebpf] {json.dumps(record)}", flush=True)

    listener = _FeedingListener()
    listener.b["events"].open_perf_buffer(listener.handle_event)
    while not stop_event.is_set():
        try:
            listener.b.perf_buffer_poll(timeout=200)
        except KeyboardInterrupt:
            break


# ---------------------------------------------------------------------------
# Reconcile loop
# ---------------------------------------------------------------------------
def _reconcile_loop(store: StateStore, interval: float, shadow: bool, stop_event: threading.Event):
    """
    Main control loop: every `interval` seconds, run confidence → reconcile →
    actions → trace_logger for all containers with recorded events.

    shadow=False (default): runs BOTH real and shadow paths every cycle.
    shadow=True  (--shadow): runs ONLY the shadow path — no real actions.
    """
    cycle = 0
    while not stop_event.is_set():
        time.sleep(interval)
        if stop_event.is_set():
            break

        cycle += 1
        print(f"\n{'─'*50}", flush=True)
        print(f"[main] reconcile cycle #{cycle}  (shadow_only={shadow})", flush=True)

        summary = store.summary()
        if not summary:
            print("[main] no events recorded yet — waiting...", flush=True)
            continue

        print(f"[main] state snapshot: {summary}", flush=True)

        if shadow:
            # Shadow-only mode: compute decisions but never execute real actions
            print("--- Shadow-only path ---", flush=True)
            decisions = reconcile(store, shadow=True)
            for decision in decisions:
                result = execute(decision)   # execute() skips action for shadow mode
                log_decision(decision, result)
            print(f"[main] cycle #{cycle} complete (shadow-only). Traces → traces.jsonl", flush=True)
        else:
            # Default: run BOTH real and shadow paths every cycle
            both = reconcile_both(store)

            # Execute and log real path
            real_decisions = both["real"]
            real_results = []
            for decision in real_decisions:
                result = execute(decision)
                log_decision(decision, result)
                real_results.append(result)

            # Execute (no-op) and log shadow path
            shadow_decisions = both["shadow"]
            for decision in shadow_decisions:
                result = execute(decision)   # execute() skips action for shadow mode
                log_decision(decision, result)

            # Per-cycle summary: real vs shadow side-by-side
            print(f"\n[main] cycle #{cycle} summary — real vs shadow:", flush=True)
            real_by   = {d["container"]: d for d in real_decisions}
            shadow_by = {d["container"]: d for d in shadow_decisions}
            for c in sorted(real_by):
                r_action = real_by[c]["action"]
                s_action = shadow_by.get(c, {}).get("action", "?")
                if r_action != s_action:
                    _div = {
                        "time":          time.time(),
                        "cycle":         cycle,
                        "container":     c,
                        "real_action":   r_action,
                        "shadow_action": s_action,
                        "track":         "S",
                    }
                    with open(DIVERGENCE_LOG, "a") as _df:
                        _df.write(json.dumps(_div) + "\n")
                    print(
                        f"  container={c}  [REAL]={r_action}  [SHADOW]={s_action}"
                        f"  *** DIVERGENCE ***",
                        flush=True,
                    )
                else:
                    print(
                        f"  container={c}  [REAL]={r_action}  [SHADOW]={s_action}",
                        flush=True,
                    )
            print(f"[main] cycle #{cycle} complete. Traces → traces.jsonl", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="UnderCurrent Control Plane")
    parser.add_argument("--real",     action="store_true", help="Use live eBPF events (root required)")
    parser.add_argument("--shadow",   action="store_true", help="Shadow mode: log decisions, skip real actions")
    parser.add_argument("--interval", type=float, default=5.0, help="Reconcile interval in seconds (default: 5)")
    parser.add_argument("--rate",     type=float, default=1.5, help="[simulate] Seconds between synthetic events")
    args = parser.parse_args()

    print(BANNER, flush=True)
    mode = "REAL (eBPF)" if args.real else "SIMULATE"
    print(f"[main] mode={mode}  interval={args.interval}s  shadow={args.shadow}", flush=True)

    store      = StateStore(window_seconds=WINDOW_SECONDS)
    stop_event = threading.Event()

    # Start event source in background thread
    if args.real:
        source_thread = threading.Thread(
            target=_real_events, args=(store, stop_event), daemon=True
        )
    else:
        source_thread = threading.Thread(
            target=_simulate_events, args=(store, stop_event, args.rate), daemon=True
        )

    # Start reconcile loop in background thread
    reconcile_thread = threading.Thread(
        target=_reconcile_loop,
        args=(store, args.interval, args.shadow, stop_event),
        daemon=True,
    )

    source_thread.start()
    reconcile_thread.start()
    print("[main] running — press Ctrl+C to stop\n", flush=True)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[main] shutting down...", flush=True)
        stop_event.set()
        source_thread.join(timeout=3)
        reconcile_thread.join(timeout=3)
        print("[main] done.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
