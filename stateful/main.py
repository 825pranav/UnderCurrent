# main.py — F8: Stateful Control Plane Orchestrator
#
# Wires the full stateful pipeline:
#   EventListener (simulate/real) → StatefulStateStore → Confidence
#   → FSM-gated Reconcile → Actions → TraceLogger
#
# Modes:
#   --simulate   (default) Synthetic stateful events; no BPF / root required
#   --real                 Live eBPF probes (requires root + bcc)
#   --shadow               Dry-run: compute decisions, suppress execution,
#                          log divergence between shadow and real paths
#
# Usage:
#   python3 main.py                   # simulate mode
#   python3 main.py --real            # real eBPF (root required)
#   python3 main.py --shadow          # dry-run
#   python3 main.py --interval 10     # reconcile every 10 s (default: 5 s)
#   python3 main.py --rate 1.5        # synthetic event every ~1.5 s (default)

import argparse
import json
import os
import random
import sys
import threading
import time

from state_store import StatefulStateStore
from fsm import ContainerFSM
from reconcile import reconcile, reconcile_both
from actions import execute
from trace_logger import log_decision

WINDOW_SECONDS = 60
DIVERGENCE_LOG = os.path.join(os.path.dirname(__file__), "divergence_log.jsonl")

BANNER = """
╔══════════════════════════════════════════════════╗
║     UnderCurrent — Stateful Control Plane        ║
║   Risk-Aware Autonomic Remediation (Type F)      ║
╚══════════════════════════════════════════════════╝
"""

# ── Simulated workload config ──────────────────────────────────────────────────
SIMULATED_CONTAINERS = ["postgres", "mongo", "redis-persist", "kafka-broker", "etcd"]

# Weighted event distribution: high proportion of I/O latency events,
# occasional VFS errors, rare mount loss (realistic for stateful workloads)
_EVENT_WEIGHTS = [
    ("blk_io_latency",     0.55),
    ("vfs_write_error",    0.18),
    ("vfs_read_error",     0.12),
    ("volume_mount_lost",  0.05),   # rare but high-impact
    ("volume_mount_restored", 0.10),
]
_EVENT_TYPES  = [e for e, _ in _EVENT_WEIGHTS]
_EVENT_PROBS  = [w for _, w in _EVENT_WEIGHTS]


def _weighted_choice(types, weights):
    import random as _r
    r = _r.random()
    cumulative = 0.0
    for t, w in zip(types, weights):
        cumulative += w
        if r < cumulative:
            return t
    return types[-1]


def _simulate_events(store: StatefulStateStore, stop_event: threading.Event,
                     rate: float = 1.5):
    """
    Background thread: emit synthetic stateful events into the StateStore.
    Latency distribution is bimodal — most events are normal, ~20% are high-latency
    to ensure the confidence scorer has meaningful signal to work with.
    """
    while not stop_event.is_set():
        container = random.choice(SIMULATED_CONTAINERS)
        ev_type   = _weighted_choice(_EVENT_TYPES, _EVENT_PROBS)

        event = {
            "container":  container,
            "pid":        random.randint(1000, 99999),
            "event":      ev_type,
            "time":       time.time(),
            "node_type":  "F",
        }

        if ev_type == "blk_io_latency":
            # ~20% chance of a high-latency event
            if random.random() < 0.20:
                event["latency_us"] = random.randint(10_000, 120_000)  # 10–120 ms
            else:
                event["latency_us"] = random.randint(100, 4_000)       # 0.1–4 ms

        if ev_type in ("volume_mount_lost", "volume_mount_restored"):
            event["volume_path"] = "/data"

        store.record(event)
        print(f"  [sim-f] {json.dumps(event)}", flush=True)
        stop_event.wait(random.uniform(rate * 0.5, rate * 1.5))


# ── Real eBPF event source ─────────────────────────────────────────────────────
def _real_events(store: StatefulStateStore, stop_event: threading.Event):
    """
    Background thread: capture live kernel events via eBPF and feed StatefulStateStore.
    Requires root and the bcc library.
    """
    try:
        from event_listener import EventListener, _VOL_ETYPE
    except ImportError as e:
        print(f"[main-f] ERROR: cannot import event_listener — {e}")
        print("[main-f] Run without --real, or install bcc (requires root).")
        stop_event.set()
        return

    class _FeedingListener(EventListener):
        """Subclass that feeds events into StatefulStateStore instead of printing."""
        def handle_event(self, cpu, data, size):
            ev_raw = self.b["f_events"].event(data)
            ev_type = {0: "blk_io_latency", 1: "vfs_write_error",
                       2: "vfs_read_error"}.get(ev_raw.type, "unknown")
            record = {
                "container":  ev_raw.comm.decode("utf-8", errors="replace"),
                "pid":        ev_raw.pid,
                "event":      ev_type,
                "time":       time.time(),
                "latency_us": ev_raw.latency_ns // 1000 if ev_raw.type == 0 else 0,
                "node_type":  "F",
            }
            store.record(record)
            print(f"  [ebpf-f] {json.dumps(record)}", flush=True)

        def handle_vol_event(self, cpu, data, size):
            ev_raw  = self.b["vol_events"].event(data)
            ev_type = _VOL_ETYPE.get(ev_raw.type, "unknown")
            record  = {
                "container":   ev_raw.comm.decode("utf-8", errors="replace"),
                "pid":         ev_raw.pid,
                "event":       ev_type,
                "time":        time.time(),
                "volume_path": "",   # not available without bpf_probe_read_user_str
                "node_type":   "F",
            }
            store.record(record)
            print(f"  [ebpf-f] {json.dumps(record)}", flush=True)

    listener = _FeedingListener()
    listener.b["f_events"].open_perf_buffer(listener.handle_event)
    listener.b["vol_events"].open_perf_buffer(listener.handle_vol_event)
    while not stop_event.is_set():
        try:
            listener.b.perf_buffer_poll(timeout=200)
        except KeyboardInterrupt:
            break


# ── Reconcile loop ─────────────────────────────────────────────────────────────
def _reconcile_loop(store: StatefulStateStore, fsm: ContainerFSM,
                    interval: float, shadow: bool, stop_event: threading.Event):
    """
    Main control loop: every `interval` seconds, run the full pipeline for all
    containers with recorded events.

    Post-action FSM transitions:
      - Successful checkpoint_and_restart → fire "recover" (Repairing → Recovered)
      - Failed  checkpoint_and_restart   → fire "degrade"  (re-enter Degraded)

    Shadow divergence: when shadow=True, also run the real path in memory and
    log any divergence (different action chosen) to stdout for research metrics.
    """
    cycle = 0
    while not stop_event.is_set():
        stop_event.wait(interval)
        if stop_event.is_set():
            break

        cycle += 1
        print(f"\n{'─' * 52}", flush=True)
        print(f"[main-f] reconcile cycle #{cycle}  (shadow={shadow})", flush=True)

        summary = store.summary()
        if not summary:
            print("[main-f] no events yet — waiting...", flush=True)
            continue

        print(f"[main-f] state snapshot: {summary}", flush=True)
        print(f"[main-f] FSM snapshot:   {fsm.snapshot()}", flush=True)

        if shadow:
            # ── Shadow-only mode: compute decisions, never fire real actions ──
            print("--- Shadow-only path ---", flush=True)
            decisions = reconcile(store, ContainerFSM(), shadow=True)
            for decision in decisions:
                result = execute(decision)   # execute() skips action for shadow mode
                log_decision(decision, result)
        else:
            # ── Default: run BOTH real and shadow paths every cycle ────────────
            both = reconcile_both(store, fsm)
            real_decisions   = both["real"]
            shadow_decisions = both["shadow"]

            # Execute and log real path; apply post-action FSM transitions
            real_results = []
            for decision in real_decisions:
                result = execute(decision)
                log_decision(decision, result)
                real_results.append(result)

                cname  = decision["container"]
                action = decision["action"]
                if action == "checkpoint_and_restart":
                    if result.get("success"):
                        t = fsm.apply(cname, "recover")
                        if t:
                            print(f"[main-f] FSM recover: {cname} "
                                  f"{t[0]}→{t[1]}", flush=True)
                    else:
                        fsm.apply(cname, "degrade")   # repair failed → back to Degraded

            # Execute (no-op) and log shadow path
            for decision in shadow_decisions:
                result = execute(decision)   # no-op in shadow mode
                log_decision(decision, result)

            # ── Divergence detection ──────────────────────────────────────────
            real_by   = {d["container"]: d for d in real_decisions}
            shadow_by = {d["container"]: d for d in shadow_decisions}
            print(f"\n[main-f] cycle #{cycle} summary — real vs shadow:", flush=True)
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
                        "track":         "F",
                    }
                    with open(DIVERGENCE_LOG, "a") as _df:
                        _df.write(json.dumps(_div) + "\n")
                    print(
                        f"[DIVERGENCE] container={c} real={r_action} shadow={s_action}",
                        flush=True,
                    )
                else:
                    print(
                        f"  container={c}  [REAL-F]={r_action}  [SHADOW-F]={s_action}",
                        flush=True,
                    )

        print(f"[main-f] cycle #{cycle} complete — traces → stateful/traces.jsonl",
              flush=True)


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="UnderCurrent Stateful Control Plane")
    parser.add_argument("--real",     action="store_true",
                        help="Use live eBPF events (root required)")
    parser.add_argument("--shadow",   action="store_true",
                        help="Dry-run: log decisions only, skip execution")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Reconcile interval in seconds (default: 5)")
    parser.add_argument("--rate",     type=float, default=1.5,
                        help="[simulate] seconds between synthetic events")
    args = parser.parse_args()

    print(BANNER, flush=True)
    mode = "REAL (eBPF)" if args.real else "SIMULATE"
    print(f"[main-f] mode={mode}  interval={args.interval}s  shadow={args.shadow}",
          flush=True)

    store      = StatefulStateStore(window_seconds=WINDOW_SECONDS)
    fsm        = ContainerFSM()
    stop_event = threading.Event()

    if args.real:
        source_thread = threading.Thread(
            target=_real_events, args=(store, stop_event), daemon=True,
        )
    else:
        source_thread = threading.Thread(
            target=_simulate_events, args=(store, stop_event, args.rate), daemon=True,
        )

    reconcile_thread = threading.Thread(
        target=_reconcile_loop,
        args=(store, fsm, args.interval, args.shadow, stop_event),
        daemon=True,
    )

    source_thread.start()
    reconcile_thread.start()
    print("[main-f] running — press Ctrl+C to stop\n", flush=True)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[main-f] shutting down...", flush=True)
        stop_event.set()
        source_thread.join(timeout=3)
        reconcile_thread.join(timeout=3)
        print("[main-f] done.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
