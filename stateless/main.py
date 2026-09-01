# stateless/main.py — UnderCurrent Type-S Control Plane
#
# Pipeline:  EventListener → StateStore → Confidence → Reconcile → Actions → TraceLogger
#
# Modes (--mode):
#   real        (default)  Execute real remediation actions. No shadow overhead.
#   shadow                 Dry-run only — compute decisions, never execute them.
#   divergence             Run both paths every cycle; log where they disagree.
#
# Event source:
#   (default)  Synthetic simulation — no BPF / root required.
#   --real     Live eBPF probes (requires root + bcc).
#
# Examples:
#   python3 main.py                      # real mode, simulated events
#   python3 main.py --real               # real mode, live eBPF  (root required)
#   python3 main.py --mode shadow        # shadow/dry-run
#   python3 main.py --mode divergence    # side-by-side research mode
#   python3 main.py --interval 10        # reconcile every 10 s

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

WINDOW_SECONDS = 60
DIVERGENCE_LOG = os.path.join(os.path.dirname(__file__), "divergence_log.jsonl")

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_DIM    = "\033[2m"

BANNER = f"""{_CYAN}{_BOLD}
╔══════════════════════════════════════════════╗
║     UnderCurrent — Type-S Control Plane      ║
║     Stateless · Risk-Aware Remediation       ║
╚══════════════════════════════════════════════╝{_RESET}"""

SIMULATED_CONTAINERS = ["nginx"]   # the only real Type-S workload (see docker-compose.yml)
SIMULATED_EVENTS     = ["process_exit", "tcp_connect", "tcp_connect"]


# ── Event sources ──────────────────────────────────────────────────────────────

def _simulate_events(store: StateStore, stop_event: threading.Event, rate: float = 1.5):
    """Emit synthetic events into the StateStore (no root required)."""
    while not stop_event.is_set():
        event = {
            "container": random.choice(SIMULATED_CONTAINERS),
            "pid":        random.randint(1000, 99999),
            "event":      random.choice(SIMULATED_EVENTS),
            "time":       time.time(),
            "node_type":  "S",
        }
        store.record(event)
        print(f"  {_DIM}[sim-s] {json.dumps(event)}{_RESET}", flush=True)
        time.sleep(random.uniform(rate * 0.5, rate * 1.5))


def _real_events(store: StateStore, stop_event: threading.Event):
    """Capture live kernel events via eBPF and feed the StateStore (root required)."""
    try:
        from event_listener import EventListener, _DOCKER_COMM_MAP
    except ImportError as exc:
        print(f"{_RED}[main-s] ERROR: cannot import event_listener — {exc}{_RESET}")
        print(f"{_YELLOW}[main-s] Install bcc and run as root, or drop --real.{_RESET}")
        stop_event.set()
        return

    class _FeedingListener(EventListener):
        def __init__(self):
            super().__init__()
            print(f"[ebpf-s] filter active — containers: {sorted(_DOCKER_COMM_MAP.values())}", flush=True)

        def handle_event(self, cpu, data, size):
            raw  = self.b["events"].event(data)
            comm = raw.comm.decode("utf-8", errors="replace").rstrip("\x00")
            container = _DOCKER_COMM_MAP.get(comm)
            if container is None:
                return  # not a Docker container process — discard
            record = {
                "container": container,
                "pid":       raw.pid,
                "event":     "process_exit" if raw.type == 0 else "tcp_connect",
                "time":      time.time(),
                "node_type": "S",
            }
            store.record(record)
            print(f"  {_DIM}[ebpf-s] {json.dumps(record)}{_RESET}", flush=True)

    listener = _FeedingListener()
    listener.b["events"].open_perf_buffer(listener.handle_event)
    while not stop_event.is_set():
        try:
            listener.b.perf_buffer_poll(timeout=200)
        except KeyboardInterrupt:
            break


# ── Reconcile loop ─────────────────────────────────────────────────────────────

def _reconcile_loop(store: StateStore, interval: float, mode: str,
                    stop_event: threading.Event):
    """
    Main control loop — runs every `interval` seconds.

    mode="real"       Execute real remediation actions. No shadow computation.
    mode="shadow"     Dry-run: compute decisions, never execute them.
    mode="divergence" Run both paths; log where real and shadow disagree.
    """
    cycle = 0
    while not stop_event.is_set():
        stop_event.wait(interval)
        if stop_event.is_set():
            break

        cycle += 1
        print(f"\n{_DIM}{'─' * 50}{_RESET}", flush=True)
        print(f"{_BOLD}[main-s]{_RESET} cycle #{cycle}  mode={mode}", flush=True)

        summary = store.summary()
        if not summary:
            print(f"{_DIM}[main-s] no events yet — waiting...{_RESET}", flush=True)
            continue

        print(f"{_DIM}[main-s] snapshot: {summary}{_RESET}", flush=True)

        if mode == "shadow":
            decisions = reconcile(store, shadow=True)
            for d in decisions:
                result = execute(d)
                log_decision(d, result)
            _print_decisions("shadow", decisions)

        elif mode == "divergence":
            both           = reconcile_both(store)
            real_decisions = both["real"]
            shad_decisions = both["shadow"]

            for d in real_decisions:
                log_decision(d, execute(d))
            for d in shad_decisions:
                log_decision(d, execute(d))

            _print_divergence(cycle, real_decisions, shad_decisions, track="S")

        else:  # real (default)
            decisions = reconcile(store, shadow=False)
            for d in decisions:
                result = execute(d)
                log_decision(d, result)
            _print_decisions("real", decisions)

        print(f"{_DIM}[main-s] traces → stateless/traces.jsonl{_RESET}", flush=True)


def _print_decisions(mode: str, decisions: list):
    label = f"{_GREEN}REAL{_RESET}" if mode == "real" else f"{_YELLOW}SHADOW{_RESET}"
    for d in decisions:
        action = d["action"]
        score  = d.get("score", 0.0)
        c      = d["container"]
        color  = _RED if action != "no_action" else _DIM
        print(f"  [{label}] {c:20s}  action={color}{action}{_RESET}  score={score:.2f}",
              flush=True)


def _print_divergence(cycle: int, real_decisions: list, shad_decisions: list,
                      track: str = "S"):
    real_by   = {d["container"]: d for d in real_decisions}
    shadow_by = {d["container"]: d for d in shad_decisions}
    print(f"\n[main-s] cycle #{cycle} — real vs shadow:", flush=True)
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
                "track":         track,
            }
            with open(DIVERGENCE_LOG, "a") as f:
                f.write(json.dumps(_div) + "\n")
            print(f"  {_RED}[DIVERGE]{_RESET} {c:20s}  "
                  f"real={_GREEN}{r_action}{_RESET}  "
                  f"shadow={_YELLOW}{s_action}{_RESET}", flush=True)
        else:
            print(f"  {_DIM}[match  ]{_RESET} {c:20s}  "
                  f"real={r_action}  shadow={s_action}", flush=True)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="UnderCurrent — Type-S (Stateless) Control Plane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
modes:
  real        Execute real remediation actions (default).
  shadow      Dry-run: compute decisions but never act on them.
  divergence  Run both paths each cycle and log where they disagree.

event source:
  (default)   Synthetic simulation — no root required.
  --real      Live eBPF probes — requires root + bcc.
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["real", "shadow", "divergence"],
        default="real",
        metavar="MODE",
        help="Execution mode: real (default) | shadow | divergence",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use live eBPF events instead of simulation (root required)",
    )
    parser.add_argument(
        "--interval",
        type=float, default=5.0,
        help="Reconcile interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--rate",
        type=float, default=1.5,
        help="[simulate] Seconds between synthetic events (default: 1.5)",
    )
    args = parser.parse_args()

    print(BANNER, flush=True)

    src_label  = f"{_RED}eBPF (root){_RESET}"    if args.real else f"{_CYAN}simulate{_RESET}"
    mode_label = {
        "real":       f"{_GREEN}real — actions will execute{_RESET}",
        "shadow":     f"{_YELLOW}shadow — dry-run, no execution{_RESET}",
        "divergence": f"{_CYAN}divergence — both paths, logging disagreements{_RESET}",
    }[args.mode]
    print(f"  source   : {src_label}", flush=True)
    print(f"  mode     : {mode_label}", flush=True)
    print(f"  interval : {args.interval} s", flush=True)
    if not args.real:
        print(f"  sim rate : {args.rate} s / event", flush=True)
    print(flush=True)

    store      = StateStore(window_seconds=WINDOW_SECONDS)
    stop_event = threading.Event()

    source_thread = threading.Thread(
        target=_real_events if args.real else _simulate_events,
        args=(store, stop_event) if args.real else (store, stop_event, args.rate),
        daemon=True,
    )
    reconcile_thread = threading.Thread(
        target=_reconcile_loop,
        args=(store, args.interval, args.mode, stop_event),
        daemon=True,
    )

    source_thread.start()
    reconcile_thread.start()
    print(f"[main-s] running — press {_BOLD}Ctrl+C{_RESET} to stop\n", flush=True)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n[main-s] shutting down...", flush=True)
        stop_event.set()
        source_thread.join(timeout=3)
        reconcile_thread.join(timeout=3)
        print("[main-s] done.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
