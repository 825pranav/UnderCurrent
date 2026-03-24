# dashboard.py — S9: Rich live terminal dashboard
# Replaces main.py for demos — same pipeline, live-updating UI.
#
# Install:  pip install rich
# Run:      python3 dashboard.py
#           python3 dashboard.py --real      (eBPF, root required)
#           python3 dashboard.py --shadow    (dry-run only)

import argparse
import random
import sys
import threading
import time
from collections import deque, defaultdict
from datetime import datetime

from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from state_store import StateStore
from confidence import compute_confidence, score_all
from reconcile import _decide
from actions import execute
from trace_logger import log_decision as _log_decision

# ── Shared state ────────────────────────────────────────────────────────────
_state = {
    "start":         time.time(),
    "cycle":         0,
    "total_events":  0,
    "next_cycle_in": 0,
    "action_counts": {"no_action": 0, "restart": 0, "reschedule": 0},
    # { container: {"score": float, "failures": int, "last_action": str, "last_ts": float} }
    "containers":    {},
    "log":           deque(maxlen=20),   # mixed event + decision lines
}
_state_lock = threading.Lock()

SIMULATED_CONTAINERS = ["nginx", "pyapp", "redis", "worker", "gateway"]
SIMULATED_EVENTS     = ["process_exit", "tcp_connect_fail", "tcp_connect_fail"]

# ── Stdout capture (suppresses sub-module prints during Live display) ────────
class _Capture:
    def write(self, text: str):
        text = text.strip()
        if text:
            ts = datetime.now().strftime("%H:%M:%S")
            with _state_lock:
                _state["log"].append(f"[dim]{ts}[/dim]  {text}")
    def flush(self): pass

# ── Event source threads ─────────────────────────────────────────────────────
def _simulate(store: StateStore, stop: threading.Event, rate: float):
    while not stop.is_set():
        ev = {
            "container": random.choice(SIMULATED_CONTAINERS),
            "pid":        random.randint(1000, 99999),
            "event":      random.choice(SIMULATED_EVENTS),
            "time":       time.time(),
        }
        store.record(ev)
        ts = datetime.now().strftime("%H:%M:%S")
        line = (
            f"[dim]{ts}[/dim]  [cyan][event][/cyan]  "
            f"[bold]{ev['container']:<10}[/bold] "
            f"{ev['event']:<20}  pid={ev['pid']}"
        )
        with _state_lock:
            _state["log"].append(line)
            _state["total_events"] += 1
        stop.wait(random.uniform(rate * 0.5, rate * 1.5))


def _real(store: StateStore, stop: threading.Event):
    try:
        from event_listener import EventListener
    except ImportError as exc:
        with _state_lock:
            _state["log"].append(f"[red]ERROR: {exc}. Run with --simulate.[/red]")
        stop.set()
        return

    class _Feed(EventListener):
        def handle_event(self, cpu, data, size):
            import time as _t
            raw = self.b["events"].event(data)
            ev = {
                "container": raw.comm.decode("utf-8", errors="replace"),
                "pid":       raw.pid,
                "event":     "process_exit" if raw.type == 0 else "tcp_connect_fail",
                "time":      _t.time(),
            }
            store.record(ev)
            ts = datetime.now().strftime("%H:%M:%S")
            with _state_lock:
                _state["log"].append(
                    f"[dim]{ts}[/dim]  [cyan][ebpf][/cyan]  "
                    f"[bold]{ev['container']:<10}[/bold] {ev['event']}"
                )
                _state["total_events"] += 1

    listener = _Feed()
    listener.b["events"].open_perf_buffer(listener.handle_event)
    while not stop.is_set():
        listener.b.perf_buffer_poll(timeout=200)


# ── Reconcile loop ────────────────────────────────────────────────────────────
def _reconcile(store: StateStore, interval: float, shadow: bool, stop: threading.Event):
    from reconcile import reconcile_both as _reconcile_both

    while not stop.is_set():
        # Countdown display
        for remaining in range(int(interval), 0, -1):
            with _state_lock:
                _state["next_cycle_in"] = remaining
            if stop.wait(1):
                return

        with _state_lock:
            _state["cycle"] += 1
            _state["next_cycle_in"] = 0

        scores = score_all(store)
        ts = datetime.now().strftime("%H:%M:%S")

        if shadow:
            # Shadow-only mode: compute but never execute
            for container, score in scores.items():
                decision = _decide(container, score)
                decision["mode"] = "shadow"
                result = execute(decision)
                _log_decision(decision, result)
                action = decision["action"]
                color  = {"reschedule": "red", "restart": "yellow", "no_action": "green"}.get(action, "white")
                line = (
                    f"[dim]{ts}[/dim]  [dim magenta][SHADOW][/dim magenta]  "
                    f"[bold]{container:<10}[/bold]  score=[bold]{score}[/bold]  "
                    f"→ [{color}]{action}[/{color}]"
                )
                with _state_lock:
                    _state["containers"][container] = {
                        "score":       score,
                        "failures":    store.get_failure_count(container),
                        "last_action": f"[SHADOW] {action}",
                        "last_ts":     time.time(),
                    }
                    _state["action_counts"][action] = _state["action_counts"].get(action, 0) + 1
                    _state["log"].append(line)
        else:
            # Default mode: run BOTH real and shadow paths
            both = _reconcile_both(store)
            real_by   = {d["container"]: d for d in both["real"]}
            shadow_by = {d["container"]: d for d in both["shadow"]}

            for container, score in scores.items():
                # Real path
                r_decision = real_by.get(container)
                if r_decision:
                    r_result = execute(r_decision)
                    _log_decision(r_decision, r_result)
                    r_action = r_decision["action"]
                else:
                    r_action = "?"

                # Shadow path
                s_decision = shadow_by.get(container)
                if s_decision:
                    s_result = execute(s_decision)
                    _log_decision(s_decision, s_result)
                    s_action = s_decision["action"]
                else:
                    s_action = "?"

                color  = {"reschedule": "red", "restart": "yellow", "no_action": "green"}.get(r_action, "white")
                s_color = {"reschedule": "red", "restart": "yellow", "no_action": "green"}.get(s_action, "white")
                diverge_note = ""
                if r_action != s_action:
                    diverge_note = f"  [bold red]DIVERGE[/bold red] shadow=[{s_color}]{s_action}[/{s_color}]"

                line = (
                    f"[dim]{ts}[/dim]  [magenta][REAL][/magenta]  "
                    f"[bold]{container:<10}[/bold]  score=[bold]{score}[/bold]  "
                    f"→ [{color}]{r_action}[/{color}]{diverge_note}"
                )
                with _state_lock:
                    _state["containers"][container] = {
                        "score":       score,
                        "failures":    store.get_failure_count(container),
                        "last_action": r_action,
                        "last_ts":     time.time(),
                    }
                    _state["action_counts"][r_action] = _state["action_counts"].get(r_action, 0) + 1
                    _state["log"].append(line)


# ── Rich layout builders ──────────────────────────────────────────────────────
def _header(mode: str, shadow: bool) -> Panel:
    uptime = int(time.time() - _state["start"])
    h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
    with _state_lock:
        cycle  = _state["cycle"]
        events = _state["total_events"]
    content = Text(justify="center")
    content.append("UnderCurrent", style="bold white")
    content.append("  ·  Risk-Aware Autonomic Control Plane\n", style="dim")
    content.append(
        f"mode: {mode}   shadow: {shadow}   "
        f"uptime: {h:02d}:{m:02d}:{s:02d}   "
        f"cycle: {cycle}   events: {events}",
        style="dim cyan"
    )
    return Panel(Align.center(content), style="bold blue", box=box.DOUBLE_EDGE)


def _container_table() -> Panel:
    table = Table(box=box.SIMPLE_HEAD, expand=True, show_footer=False)
    table.add_column("Container",    style="bold white",  min_width=12)
    table.add_column("Failures",     justify="right",     min_width=8)
    table.add_column("Score",        justify="right",     min_width=7)
    table.add_column("Risk",         justify="center",    min_width=10)
    table.add_column("Last Action",  justify="center",    min_width=12)

    with _state_lock:
        rows = list(_state["containers"].items())

    if not rows:
        table.add_row("[dim]waiting for events...[/dim]", "", "", "", "")
    else:
        rows.sort(key=lambda x: x[1]["score"], reverse=True)
        for container, info in rows:
            score  = info["score"]
            action = info["last_action"]

            if score >= 0.95:
                risk_label = "[bold red]CRITICAL[/bold red]"
            elif score >= 0.80:
                risk_label = "[bold yellow]WARNING[/bold yellow]"
            elif score >= 0.40:
                risk_label = "[yellow]ELEVATED[/yellow]"
            else:
                risk_label = "[green]OK[/green]"

            action_fmt = {
                "reschedule": "[red]reschedule[/red]",
                "restart":    "[yellow]restart[/yellow]",
                "no_action":  "[dim]—[/dim]",
            }.get(action, action)

            table.add_row(
                container,
                str(info["failures"]),
                f"{score:.2f}",
                risk_label,
                action_fmt,
            )

    return Panel(table, title="[bold]Container Risk Status[/bold]", border_style="blue")


def _stats_panel(interval: float) -> Panel:
    with _state_lock:
        counts      = dict(_state["action_counts"])
        next_in     = _state["next_cycle_in"]
        total_ev    = _state["total_events"]
        cycle       = _state["cycle"]

    total_actions = sum(counts.values())
    lines = Text()
    lines.append(f"  Total events:     {total_ev}\n",                  style="white")
    lines.append(f"  Reconcile cycles: {cycle}\n",                     style="white")
    lines.append(f"  Total decisions:  {total_actions}\n\n",           style="white")
    lines.append(f"  no_action:   {counts.get('no_action',  0)}\n",    style="green")
    lines.append(f"  restart:     {counts.get('restart',    0)}\n",    style="yellow")
    lines.append(f"  reschedule:  {counts.get('reschedule', 0)}\n\n",  style="red")
    if next_in > 0:
        lines.append(f"  Next cycle in:    {next_in}s", style="dim cyan")
    else:
        lines.append("  Reconciling now...", style="bold cyan")

    return Panel(lines, title="[bold]Stats[/bold]", border_style="blue")


def _log_panel() -> Panel:
    with _state_lock:
        lines = list(_state["log"])

    text = Text()
    for line in lines[-18:]:
        text.append(line + "\n")

    return Panel(text, title="[bold]Live Feed[/bold]", border_style="dim blue")


def _build_layout(mode: str, shadow: bool, interval: float) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header",  size=5),
        Layout(name="middle",  size=16),
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


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="UnderCurrent — Rich Dashboard")
    parser.add_argument("--real",     action="store_true")
    parser.add_argument("--shadow",   action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--rate",     type=float, default=1.5)
    args = parser.parse_args()

    mode = "REAL" if args.real else "SIMULATE"
    store = StateStore(window_seconds=60)
    stop  = threading.Event()

    # Create Console BEFORE redirecting stdout so it holds the real terminal handle
    console = Console(file=sys.__stdout__)

    # Redirect stdout so sub-module prints go to the log panel instead of the screen
    sys.stdout = _Capture()

    source_fn = _real if args.real else _simulate
    source_args = (store, stop) if args.real else (store, stop, args.rate)

    threads = [
        threading.Thread(target=source_fn,  args=source_args,                                       daemon=True),
        threading.Thread(target=_reconcile, args=(store, args.interval, args.shadow, stop),          daemon=True),
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
        print("\n[undercurrent] dashboard stopped.")


if __name__ == "__main__":
    main()
