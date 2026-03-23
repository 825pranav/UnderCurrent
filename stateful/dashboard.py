# dashboard.py — F9: Stateful Rich Terminal Dashboard
#
# Live-updating terminal UI for the stateful control plane.
# Shows: FSM state per container, I/O risk scores, action counters, live feed.
#
# Install:  pip install rich
# Run:      python3 dashboard.py
#           python3 dashboard.py --shadow
#           python3 dashboard.py --interval 10

import argparse
import random
import sys
import threading
import time
from collections import deque
from datetime import datetime

from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from state_store import StatefulStateStore
from confidence import compute_confidence, score_all
from fsm import ContainerFSM
from reconcile import _decide
from actions import execute
from trace_logger import log_decision as _log_decision

# ── Shared state ───────────────────────────────────────────────────────────────
_state = {
    "start":         time.time(),
    "cycle":         0,
    "total_events":  0,
    "next_cycle_in": 0,
    "action_counts": {
        "no_action": 0, "flush_io_queue": 0,
        "checkpoint_and_restart": 0, "escalate": 0,
    },
    # { container: {score, fsm_state, last_action, io_stats, last_ts} }
    "containers":    {},
    "log":           deque(maxlen=20),
    "divergence":    0,   # count of shadow/real divergences
}
_state_lock = threading.Lock()

SIMULATED_CONTAINERS = ["postgres", "mongo", "redis-persist", "kafka-broker", "etcd"]
_EVENT_TYPES  = ["blk_io_latency", "vfs_write_error", "vfs_read_error",
                 "volume_mount_lost", "volume_mount_restored"]
_EVENT_PROBS  = [0.55, 0.18, 0.12, 0.05, 0.10]


def _weighted_choice(types, weights):
    r = random.random()
    c = 0.0
    for t, w in zip(types, weights):
        c += w
        if r < c:
            return t
    return types[-1]


# ── Stdout suppressor ──────────────────────────────────────────────────────────
class _Capture:
    def write(self, text: str):
        text = text.strip()
        if text:
            ts = datetime.now().strftime("%H:%M:%S")
            with _state_lock:
                _state["log"].append(f"[dim]{ts}[/dim]  {text}")
    def flush(self): pass


# ── Event source ───────────────────────────────────────────────────────────────
def _simulate(store: StatefulStateStore, stop: threading.Event, rate: float):
    while not stop.is_set():
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
            event["latency_us"] = (
                random.randint(10_000, 120_000) if random.random() < 0.20
                else random.randint(100, 4_000)
            )
        if ev_type in ("volume_mount_lost", "volume_mount_restored"):
            event["volume_path"] = "/data"

        store.record(event)
        ts   = datetime.now().strftime("%H:%M:%S")
        icon = {"blk_io_latency": "💾", "vfs_write_error": "✗",
                "vfs_read_error": "✗", "volume_mount_lost": "⚠",
                "volume_mount_restored": "✓"}.get(ev_type, "·")
        line = (
            f"[dim]{ts}[/dim]  [cyan][event][/cyan]  "
            f"[bold]{container:<14}[/bold] {icon} {ev_type}"
        )
        if ev_type == "blk_io_latency":
            line += f"  [dim]{event['latency_us']} µs[/dim]"

        with _state_lock:
            _state["log"].append(line)
            _state["total_events"] += 1
        stop.wait(random.uniform(rate * 0.5, rate * 1.5))


# ── Reconcile loop ─────────────────────────────────────────────────────────────
def _reconcile(store: StatefulStateStore, fsm: ContainerFSM,
               interval: float, shadow: bool, stop: threading.Event):
    while not stop.is_set():
        for remaining in range(int(interval), 0, -1):
            with _state_lock:
                _state["next_cycle_in"] = remaining
            if stop.wait(1):
                return

        with _state_lock:
            _state["cycle"] += 1
            _state["next_cycle_in"] = 0

        scores = score_all(store)
        ts     = datetime.now().strftime("%H:%M:%S")

        # Shadow divergence: run shadow path with a fresh FSM copy
        shadow_decisions = {
            d["container"]: d
            for d in [
                _decide(c, s, store.get_failures(c), ContainerFSM())
                for c, s in scores.items()
            ]
        }

        for container, score in scores.items():
            failures = store.get_failures(container)
            decision = _decide(container, score, failures, fsm)
            decision["mode"] = "shadow" if shadow else "real"

            result = execute(decision)
            _log_decision(decision, result)

            # Post-action FSM transitions
            if not shadow and decision["action"] == "checkpoint_and_restart":
                if result.get("success"):
                    fsm.apply(container, "recover")
                else:
                    fsm.apply(container, "degrade")

            action = decision["action"]
            color  = {
                "escalate":               "red",
                "checkpoint_and_restart": "yellow",
                "flush_io_queue":         "cyan",
                "no_action":              "green",
            }.get(action, "white")
            line = (
                f"[dim]{ts}[/dim]  [magenta][decision][/magenta]  "
                f"[bold]{container:<14}[/bold]  score=[bold]{score:.2f}[/bold]"
                f"  fsm={decision['fsm_state']}"
                f"  → [{color}]{action}[/{color}]"
            )

            # Check divergence
            shadow_action = shadow_decisions.get(container, {}).get("action")
            diverged = shadow_action and shadow_action != action
            if diverged:
                line += f"  [dim](shadow would: {shadow_action})[/dim]"
                with _state_lock:
                    _state["divergence"] += 1

            with _state_lock:
                _state["containers"][container] = {
                    "score":       score,
                    "fsm_state":   fsm.state(container),
                    "last_action": action,
                    "io_stats":    store.get_io_latency_stats(container),
                    "last_ts":     time.time(),
                }
                _state["action_counts"][action] = (
                    _state["action_counts"].get(action, 0) + 1
                )
                _state["log"].append(line)


# ── FSM colour map ─────────────────────────────────────────────────────────────
_FSM_COLOR = {
    "Healthy":   "green",
    "Degraded":  "yellow",
    "Audited":   "cyan",
    "Repairing": "magenta",
    "Recovered": "blue",
}


# ── Rich layout builders ───────────────────────────────────────────────────────
def _header(mode: str, shadow: bool) -> Panel:
    uptime = int(time.time() - _state["start"])
    h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
    with _state_lock:
        cycle = _state["cycle"]
        events = _state["total_events"]
        diverg = _state["divergence"]
    content = Text(justify="center")
    content.append("UnderCurrent — Stateful Control Plane (Type F)\n", style="bold white")
    content.append(
        f"mode: {mode}   shadow: {shadow}   "
        f"uptime: {h:02d}:{m:02d}:{s:02d}   "
        f"cycle: {cycle}   events: {events}   divergence: {diverg}",
        style="dim cyan",
    )
    return Panel(Align.center(content), style="bold blue", box=box.DOUBLE_EDGE)


def _container_table() -> Panel:
    table = Table(box=box.SIMPLE_HEAD, expand=True)
    table.add_column("Container",    style="bold white", min_width=14)
    table.add_column("FSM State",    justify="center",   min_width=11)
    table.add_column("Score",        justify="right",    min_width=7)
    table.add_column("Risk",         justify="center",   min_width=10)
    table.add_column("IO High/s",    justify="right",    min_width=10)
    table.add_column("Last Action",  justify="center",   min_width=22)

    with _state_lock:
        rows = list(_state["containers"].items())

    if not rows:
        table.add_row("[dim]waiting for events...[/dim]", "", "", "", "", "")
    else:
        rows.sort(key=lambda x: x[1]["score"], reverse=True)
        for container, info in rows:
            score  = info["score"]
            action = info["last_action"]
            fsm_s  = info.get("fsm_state", "Healthy")
            high_c = info.get("io_stats", {}).get("high_count", 0)

            if score >= 0.95:
                risk = "[bold red]CRITICAL[/bold red]"
            elif score >= 0.80:
                risk = "[bold yellow]WARNING[/bold yellow]"
            elif score >= 0.50:
                risk = "[yellow]DEGRADED[/yellow]"
            else:
                risk = "[green]OK[/green]"

            action_fmt = {
                "escalate":               "[bold red]escalate[/bold red]",
                "checkpoint_and_restart": "[yellow]checkpoint+restart[/yellow]",
                "flush_io_queue":         "[cyan]flush_io_queue[/cyan]",
                "no_action":              "[dim]—[/dim]",
            }.get(action, action)

            fsm_color = _FSM_COLOR.get(fsm_s, "white")
            table.add_row(
                container,
                f"[{fsm_color}]{fsm_s}[/{fsm_color}]",
                f"{score:.2f}",
                risk,
                str(high_c),
                action_fmt,
            )

    return Panel(table, title="[bold]Stateful Container Status[/bold]", border_style="blue")


def _stats_panel(interval: float) -> Panel:
    with _state_lock:
        counts   = dict(_state["action_counts"])
        next_in  = _state["next_cycle_in"]
        total_ev = _state["total_events"]
        cycle    = _state["cycle"]
        diverg   = _state["divergence"]

    total_actions = sum(counts.values())
    lines = Text()
    lines.append(f"  Total events:     {total_ev}\n",             style="white")
    lines.append(f"  Reconcile cycles: {cycle}\n",                style="white")
    lines.append(f"  Total decisions:  {total_actions}\n",        style="white")
    lines.append(f"  Divergences:      {diverg}\n\n",             style="yellow")
    lines.append(f"  no_action:        {counts.get('no_action', 0)}\n",               style="green")
    lines.append(f"  flush_io_queue:   {counts.get('flush_io_queue', 0)}\n",          style="cyan")
    lines.append(f"  chkpt+restart:    {counts.get('checkpoint_and_restart', 0)}\n",  style="yellow")
    lines.append(f"  escalate:         {counts.get('escalate', 0)}\n\n",              style="red")
    if next_in > 0:
        lines.append(f"  Next cycle in:    {next_in}s", style="dim cyan")
    else:
        lines.append("  Reconciling now...", style="bold cyan")

    return Panel(lines, title="[bold]Stats[/bold]", border_style="blue")


def _log_panel() -> Panel:
    with _state_lock:
        lines = list(_state["log"])
    text = Text()
    for line in lines[-16:]:
        text.append(line + "\n")
    return Panel(text, title="[bold]Live Feed[/bold]", border_style="dim blue")


def _build_layout(mode: str, shadow: bool, interval: float) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header",  size=5),
        Layout(name="middle",  size=17),
        Layout(name="log"),
    )
    layout["middle"].split_row(
        Layout(name="containers", ratio=3),
        Layout(name="stats",      ratio=1),
    )
    layout["header"].update(_header(mode, shadow))
    layout["containers"].update(_container_table())
    layout["stats"].update(_stats_panel(interval))
    layout["log"].update(_log_panel())
    return layout


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="UnderCurrent — Stateful Rich Dashboard")
    parser.add_argument("--shadow",   action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--rate",     type=float, default=1.5)
    args = parser.parse_args()

    mode  = "SIMULATE"
    store = StatefulStateStore(window_seconds=60)
    fsm   = ContainerFSM()
    stop  = threading.Event()

    console = Console(file=sys.__stdout__)
    sys.stdout = _Capture()

    threads = [
        threading.Thread(target=_simulate,  args=(store, stop, args.rate),              daemon=True),
        threading.Thread(target=_reconcile, args=(store, fsm, args.interval, args.shadow, stop), daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        with Live(
            _build_layout(mode, args.shadow, args.interval),
            console=console,
            refresh_per_second=2,
            screen=True,
        ) as live:
            while True:
                live.update(_build_layout(mode, args.shadow, args.interval))
                time.sleep(0.5)
    except KeyboardInterrupt:
        stop.set()
        sys.stdout = sys.__stdout__
        print("\n[undercurrent-f] dashboard stopped.")


if __name__ == "__main__":
    main()
