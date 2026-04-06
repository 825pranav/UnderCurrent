# integration/launcher.py — UnderCurrent Unified Launcher
#
# Starts both control-plane pipelines (Type-S + Type-F) as supervised subprocesses.
# Automatically restarts a pipeline if it crashes.
#
# Run from the repository root:
#   python3 integration/launcher.py                      # real mode (default)
#   python3 integration/launcher.py --mode shadow        # shadow / dry-run
#   python3 integration/launcher.py --mode divergence    # research — both paths
#   python3 integration/launcher.py --real               # live eBPF (root required)
#   python3 integration/launcher.py --interval 10        # reconcile every 10 s
#   python3 integration/launcher.py --no-dashboard       # skip the web dashboard
#   python3 integration/launcher.py --track stateless    # one track only

import argparse
import os
import signal
import subprocess
import sys
import time

REPO_ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATELESS        = os.path.join(REPO_ROOT, "stateless", "main.py")
STATEFUL         = os.path.join(REPO_ROOT, "stateful",  "main.py")
REACT_BACKEND    = os.path.join(REPO_ROOT, "dashboard", "backend.py")
STREAMLIT_DASH   = os.path.join(REPO_ROOT, "integration", "unified_dashboard.py")

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_DIM    = "\033[2m"

BANNER = f"""{_CYAN}{_BOLD}
╔══════════════════════════════════════════════════════╗
║     UnderCurrent — Unified Integration Launcher      ║
║     Type-S (Stateless) + Type-F (Stateful)           ║
╚══════════════════════════════════════════════════════╝{_RESET}"""


def _check(path: str, name: str) -> bool:
    if not os.path.exists(path):
        print(f"{_YELLOW}[launcher] WARNING: {name} not found at {path}{_RESET}")
        return False
    return True


def _build_cmd(script: str, mode: str, interval: float, use_real: bool) -> list:
    cmd = [sys.executable, script, "--mode", mode, "--interval", str(interval)]
    if use_real:
        cmd.append("--real")
    return cmd


def main():
    parser = argparse.ArgumentParser(
        description="UnderCurrent — Unified Pipeline Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
modes:
  real        Execute real remediation actions (default).
  shadow      Dry-run: compute decisions, never execute them.
  divergence  Run both paths each cycle; log disagreements.

tracks:
  both        Start Type-S and Type-F (default).
  stateless   Start Type-S only.
  stateful    Start Type-F only.
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
        help="Use live eBPF events (root required)",
    )
    parser.add_argument(
        "--track",
        choices=["both", "stateless", "stateful"],
        default="both",
        help="Which pipeline(s) to start (default: both)",
    )
    parser.add_argument(
        "--interval",
        type=float, default=5.0,
        help="Reconcile interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Skip opening any dashboard",
    )
    parser.add_argument(
        "--streamlit",
        action="store_true",
        help="Use the Streamlit dashboard instead of the React dashboard",
    )
    args = parser.parse_args()

    print(BANNER, flush=True)

    mode_label = {
        "real":       f"{_GREEN}real{_RESET}",
        "shadow":     f"{_YELLOW}shadow{_RESET}",
        "divergence": f"{_CYAN}divergence{_RESET}",
    }[args.mode]
    src_label = f"{_RED}eBPF{_RESET}" if args.real else f"{_DIM}simulate{_RESET}"
    print(f"  mode     : {mode_label}", flush=True)
    print(f"  source   : {src_label}", flush=True)
    print(f"  interval : {args.interval} s", flush=True)
    print(f"  track    : {args.track}", flush=True)
    print(flush=True)

    run_s = args.track in ("both", "stateless") and _check(STATELESS, "stateless/main.py")
    run_f = args.track in ("both", "stateful")  and _check(STATEFUL,  "stateful/main.py")

    if not run_s and not run_f:
        print(f"{_RED}[launcher] No pipelines found. Exiting.{_RESET}")
        sys.exit(1)

    procs: list[tuple[str, subprocess.Popen, list]] = []

    if run_s:
        cmd = _build_cmd(STATELESS, args.mode, args.interval, args.real)
        print(f"[launcher] {_GREEN}starting{_RESET} Type-S  →  {' '.join(cmd)}", flush=True)
        procs.append(("Type-S", subprocess.Popen(cmd, cwd=os.path.join(REPO_ROOT, "stateless")), cmd))

    if run_f:
        cmd = _build_cmd(STATEFUL, args.mode, args.interval, args.real)
        print(f"[launcher] {_GREEN}starting{_RESET} Type-F  →  {' '.join(cmd)}", flush=True)
        procs.append(("Type-F", subprocess.Popen(cmd, cwd=os.path.join(REPO_ROOT, "stateful")), cmd))

    if not args.no_dashboard:
        if args.streamlit and _check(STREAMLIT_DASH, "unified_dashboard.py"):
            dash_cmd = ["streamlit", "run", STREAMLIT_DASH, "--server.port", "8501"]
            print(f"[launcher] {_GREEN}starting{_RESET} Streamlit dashboard  →  http://localhost:8501", flush=True)
            procs.append(("Dashboard", subprocess.Popen(dash_cmd, cwd=REPO_ROOT), dash_cmd))
        elif not args.streamlit and _check(REACT_BACKEND, "dashboard/backend.py"):
            dash_cmd = [sys.executable, REACT_BACKEND]
            print(f"[launcher] {_GREEN}starting{_RESET} React dashboard  →  http://localhost:5050", flush=True)
            procs.append(("Dashboard", subprocess.Popen(dash_cmd, cwd=REPO_ROOT), dash_cmd))

    print(f"\n[launcher] all processes started — press {_BOLD}Ctrl+C{_RESET} to stop all\n",
          flush=True)

    def _shutdown(sig, frame):
        print(f"\n{_YELLOW}[launcher] shutting down...{_RESET}", flush=True)
        for name, proc, _ in procs:
            print(f"  stopping {name}...", flush=True)
            proc.terminate()
        for name, proc, _ in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("[launcher] done.", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        for i, (name, proc, cmd) in enumerate(procs):
            ret = proc.poll()
            if ret is not None:
                print(f"{_YELLOW}[launcher] {name} exited (code {ret}) — restarting...{_RESET}",
                      flush=True)
                cwd = (
                    os.path.join(REPO_ROOT, "stateless") if name == "Type-S"
                    else os.path.join(REPO_ROOT, "stateful") if name == "Type-F"
                    else REPO_ROOT
                )
                procs[i] = (name, subprocess.Popen(cmd, cwd=cwd), cmd)
        time.sleep(3)


if __name__ == "__main__":
    main()
