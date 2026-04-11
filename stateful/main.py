# stateful/main.py — UnderCurrent Type-F Control Plane
#
# Pipeline: EventListener → StatefulStateStore → Confidence → FSM-gated Reconcile
#           → Actions → TraceLogger
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

from state_store import StatefulStateStore
from fsm import ContainerFSM
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
╔══════════════════════════════════════════════════╗
║     UnderCurrent — Type-F Control Plane          ║
║     Stateful · FSM-Gated · Risk-Aware            ║
╚══════════════════════════════════════════════════╝{_RESET}"""

SIMULATED_CONTAINERS = ["postgres", "mongo", "redis-persist", "kafka-broker", "etcd"]

_EVENT_WEIGHTS = [
    ("blk_io_latency",       0.55),
    ("vfs_write_error",      0.18),
    ("vfs_read_error",       0.12),
    ("volume_mount_lost",    0.05),
    ("volume_mount_restored",0.10),
]
_EVENT_TYPES = [e for e, _ in _EVENT_WEIGHTS]
_EVENT_PROBS = [w for _, w in _EVENT_WEIGHTS]


def _weighted_choice(types, weights):
    r = random.random()
    cumulative = 0.0
    for t, w in zip(types, weights):
        cumulative += w
        if r < cumulative:
            return t
    return types[-1]


# ── Event sources ──────────────────────────────────────────────────────────────

def _simulate_events(store: StatefulStateStore, stop_event: threading.Event,
                     rate: float = 1.5):
    """
    Emit synthetic stateful events into the StateStore (no root required).
    Latency distribution is bimodal: ~20 % are high-latency to give the
    confidence scorer meaningful signal.
    """
    while not stop_event.is_set():
        container = random.choice(SIMULATED_CONTAINERS)
        ev_type   = _weighted_choice(_EVENT_TYPES, _EVENT_PROBS)

        event = {
            "container": container,
            "pid":       random.randint(1000, 99999),
            "event":     ev_type,
            "time":      time.time(),
            "node_type": "F",
        }

        if ev_type == "blk_io_latency":
            if random.random() < 0.20:
                event["latency_us"] = random.randint(10_000, 120_000)  # high: 10–120 ms
            else:
                event["latency_us"] = random.randint(100, 4_000)       # normal: 0.1–4 ms

        if ev_type in ("volume_mount_lost", "volume_mount_restored"):
            event["volume_path"] = "/data"

        store.record(event)
        print(f"  {_DIM}[sim-f] {json.dumps(event)}{_RESET}", flush=True)
        stop_event.wait(random.uniform(rate * 0.5, rate * 1.5))


def _real_events(store: StatefulStateStore, stop_event: threading.Event):
    """Capture live kernel events via eBPF and feed the StatefulStateStore (root required)."""
    try:
        from event_listener import EventListener, _VOL_ETYPE, _DOCKER_COMM_MAP
    except ImportError as exc:
        print(f"{_RED}[main-f] ERROR: cannot import event_listener — {exc}{_RESET}")
        print(f"{_YELLOW}[main-f] Install bcc and run as root, or drop --real.{_RESET}")
        stop_event.set()
        return

    class _FeedingListener(EventListener):
        def __init__(self):
            super().__init__()
            print("[FILTER ACTIVE] only forwarding: nginx, postgres, redis, mysql", flush=True)

        def handle_event(self, cpu, data, size):
            raw  = self.b["f_events"].event(data)
            comm = raw.comm.decode("utf-8", errors="replace").rstrip("\x00")
            container = _DOCKER_COMM_MAP.get(comm)
            if container is None:
                return  # not a Docker container process — discard
            ev = {0: "blk_io_latency", 1: "vfs_write_error", 2: "vfs_read_error"}.get(
                raw.type, "unknown"
            )
            record = {
                "container":  container,
                "pid":        raw.pid,
                "event":      ev,
                "time":       time.time(),
                "latency_us": raw.latency_ns // 1000 if raw.type == 0 else 0,
                "node_type":  "F",
            }
            store.record(record)
            print(f"  {_DIM}[ebpf-f] {json.dumps(record)}{_RESET}", flush=True)

        def handle_vol_event(self, cpu, data, size):
            raw  = self.b["vol_events"].event(data)
            comm = raw.comm.decode("utf-8", errors="replace").rstrip("\x00")
            container = _DOCKER_COMM_MAP.get(comm)
            if container is None:
                return  # not a Docker container process — discard
            record = {
                "container":   container,
                "pid":         raw.pid,
                "event":       _VOL_ETYPE.get(raw.type, "unknown"),
                "time":        time.time(),
                "volume_path": "",
                "node_type":   "F",
            }
            store.record(record)
            print(f"  {_DIM}[ebpf-f] {json.dumps(record)}{_RESET}", flush=True)

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
                    interval: float, mode: str, stop_event: threading.Event):
    """
    Main control loop — runs every `interval` seconds.

    mode="real"       Execute real remediation actions (default). FSM is mutated
                      in-place; post-action transitions (recover / re-degrade) fire.
    mode="shadow"     Dry-run: all decisions use a throwaway FSM; real FSM untouched.
    mode="divergence" Both paths run each cycle; divergences are logged to
                      divergence_log.jsonl for research analysis.
    """
    cycle = 0
    while not stop_event.is_set():
        stop_event.wait(interval)
        if stop_event.is_set():
            break

        cycle += 1
        print(f"\n{_DIM}{'─' * 52}{_RESET}", flush=True)
        print(f"{_BOLD}[main-f]{_RESET} cycle #{cycle}  mode={mode}", flush=True)

        summary = store.summary()
        if not summary:
            print(f"{_DIM}[main-f] no events yet — waiting...{_RESET}", flush=True)
            continue

        print(f"{_DIM}[main-f] snapshot : {summary}{_RESET}", flush=True)
        print(f"{_DIM}[main-f] FSM state: {fsm.snapshot()}{_RESET}", flush=True)

        if mode == "shadow":
            decisions = reconcile(store, ContainerFSM(), shadow=True)
            for d in decisions:
                log_decision(d, execute(d))
            _print_decisions("shadow", decisions)

        elif mode == "divergence":
            both           = reconcile_both(store, fsm)
            real_decisions = both["real"]
            shad_decisions = both["shadow"]

            for d in real_decisions:
                result = execute(d)
                log_decision(d, result)
                _apply_post_action_fsm(fsm, d, result)

            for d in shad_decisions:
                log_decision(d, execute(d))

            _print_divergence(cycle, real_decisions, shad_decisions, track="F")

        else:  # real (default)
            decisions = reconcile(store, fsm, shadow=False)
            for d in decisions:
                result = execute(d)
                log_decision(d, result)
                _apply_post_action_fsm(fsm, d, result)
            _print_decisions("real", decisions)

        print(f"{_DIM}[main-f] traces → stateful/traces.jsonl{_RESET}", flush=True)


def _apply_post_action_fsm(fsm: ContainerFSM, decision: dict, result: dict):
    """Fire FSM recover / re-degrade transitions after checkpoint_and_restart."""
    if decision["action"] != "checkpoint_and_restart":
        return
    cname = decision["container"]
    if result.get("success"):
        t = fsm.apply(cname, "recover")
        if t:
            print(f"[main-f] FSM: {cname} {_GREEN}{t[0]}→{t[1]}{_RESET}", flush=True)
    else:
        fsm.apply(cname, "degrade")


def _print_decisions(mode: str, decisions: list):
    label = f"{_GREEN}REAL  {_RESET}" if mode == "real" else f"{_YELLOW}SHADOW{_RESET}"
    for d in decisions:
        action = d["action"]
        score  = d.get("score", 0.0)
        c      = d["container"]
        fsm_st = d.get("fsm_state", "—")
        color  = _RED if action != "no_action" else _DIM
        print(
            f"  [{label}] {c:20s}  action={color}{action}{_RESET}"
            f"  score={score:.2f}  fsm={fsm_st}",
            flush=True,
        )


def _print_divergence(cycle: int, real_decisions: list, shad_decisions: list,
                      track: str = "F"):
    real_by   = {d["container"]: d for d in real_decisions}
    shadow_by = {d["container"]: d for d in shad_decisions}
    print(f"\n[main-f] cycle #{cycle} — real vs shadow:", flush=True)
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
            print(
                f"  {_RED}[DIVERGE]{_RESET} {c:20s}  "
                f"real={_GREEN}{r_action}{_RESET}  "
                f"shadow={_YELLOW}{s_action}{_RESET}",
                flush=True,
            )
        else:
            print(
                f"  {_DIM}[match  ]{_RESET} {c:20s}  "
                f"real={r_action}  shadow={s_action}",
                flush=True,
            )


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="UnderCurrent — Type-F (Stateful) Control Plane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
modes:
  real        Execute real remediation actions (default).
              The live FSM is mutated; post-action transitions fire normally.
  shadow      Dry-run: all decisions use a throwaway FSM; nothing executes.
  divergence  Run both paths each cycle; log FSM-induced disagreements.

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

    src_label  = f"{_RED}eBPF (root){_RESET}" if args.real else f"{_CYAN}simulate{_RESET}"
    mode_label = {
        "real":       f"{_GREEN}real — actions will execute{_RESET}",
        "shadow":     f"{_YELLOW}shadow — dry-run, no execution{_RESET}",
        "divergence": f"{_CYAN}divergence — both paths, logging FSM disagreements{_RESET}",
    }[args.mode]
    print(f"  source   : {src_label}", flush=True)
    print(f"  mode     : {mode_label}", flush=True)
    print(f"  interval : {args.interval} s", flush=True)
    if not args.real:
        print(f"  sim rate : {args.rate} s / event", flush=True)
    print(flush=True)

    store      = StatefulStateStore(window_seconds=WINDOW_SECONDS)
    fsm        = ContainerFSM()
    stop_event = threading.Event()

    source_thread = threading.Thread(
        target=_real_events if args.real else _simulate_events,
        args=(store, stop_event) if args.real else (store, stop_event, args.rate),
        daemon=True,
    )
    reconcile_thread = threading.Thread(
        target=_reconcile_loop,
        args=(store, fsm, args.interval, args.mode, stop_event),
        daemon=True,
    )

    source_thread.start()
    reconcile_thread.start()
    print(f"[main-f] running — press {_BOLD}Ctrl+C{_RESET} to stop\n", flush=True)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n[main-f] shutting down...", flush=True)
        stop_event.set()
        source_thread.join(timeout=3)
        reconcile_thread.join(timeout=3)
        print("[main-f] done.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
