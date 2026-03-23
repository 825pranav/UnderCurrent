# integration/launcher.py — Unified pipeline launcher
# Starts both stateless and stateful pipelines as subprocesses.
#
# Run from repo root:
#   python3 integration/launcher.py
#   python3 integration/launcher.py --shadow    # both tracks in shadow mode
#   python3 integration/launcher.py --interval 10

import argparse
import os
import signal
import subprocess
import sys
import time

REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATELESS  = os.path.join(REPO_ROOT, "stateless", "main.py")
STATEFUL   = os.path.join(REPO_ROOT, "stateful",  "main.py")
DASHBOARD  = os.path.join(REPO_ROOT, "integration", "unified_dashboard.py")

BANNER = """
╔══════════════════════════════════════════════════════╗
║     UnderCurrent — Unified Integration Launcher      ║
║     Stateless (S) + Stateful (F) Control Plane       ║
╚══════════════════════════════════════════════════════╝
"""


def check_pipeline(path: str, name: str) -> bool:
    if not os.path.exists(path):
        print(f"  [launcher] WARNING: {name} pipeline not found at {path}")
        return False
    return True


def build_args(base_args: list, shadow: bool, interval: float) -> list:
    args = [sys.executable] + base_args
    if shadow:
        args.append("--shadow")
    args += ["--interval", str(interval)]
    return args


def main():
    parser = argparse.ArgumentParser(description="UnderCurrent Unified Launcher")
    parser.add_argument("--shadow",   action="store_true",
                        help="Run both pipelines in shadow (dry-run) mode")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Reconcile interval in seconds (default: 5)")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="Don't open the web dashboard")
    args = parser.parse_args()

    print(BANNER)

    s_ok = check_pipeline(STATELESS, "stateless")
    f_ok = check_pipeline(STATEFUL,  "stateful")

    if not s_ok and not f_ok:
        print("[launcher] No pipelines found. Exiting.")
        sys.exit(1)

    procs = []

    if s_ok:
        cmd = build_args([STATELESS], args.shadow, args.interval)
        print(f"[launcher] Starting stateless pipeline: {' '.join(cmd)}")
        procs.append(("Stateless-S", subprocess.Popen(
            cmd, cwd=os.path.join(REPO_ROOT, "stateless")
        )))

    if f_ok:
        cmd = build_args([STATEFUL], args.shadow, args.interval)
        print(f"[launcher] Starting stateful pipeline:  {' '.join(cmd)}")
        procs.append(("Stateful-F", subprocess.Popen(
            cmd, cwd=os.path.join(REPO_ROOT, "stateful")
        )))

    if not args.no_dashboard:
        dash_cmd = ["streamlit", "run", DASHBOARD, "--server.port", "8501"]
        print(f"[launcher] Starting unified dashboard at http://localhost:8501")
        procs.append(("Dashboard", subprocess.Popen(dash_cmd, cwd=REPO_ROOT)))

    print(f"\n[launcher] All processes started. Press Ctrl+C to stop all.\n")

    def shutdown(sig, frame):
        print("\n[launcher] Shutting down all processes...")
        for name, proc in procs:
            print(f"  stopping {name}...")
            proc.terminate()
        for name, proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("[launcher] Done.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Monitor loop — restart crashed processes
    while True:
        for i, (name, proc) in enumerate(procs):
            ret = proc.poll()
            if ret is not None:
                print(f"[launcher] {name} exited with code {ret} — restarting...")
                if name == "Stateless-S" and s_ok:
                    cmd = build_args([STATELESS], args.shadow, args.interval)
                    procs[i] = (name, subprocess.Popen(
                        cmd, cwd=os.path.join(REPO_ROOT, "stateless")
                    ))
                elif name == "Stateful-F" and f_ok:
                    cmd = build_args([STATEFUL], args.shadow, args.interval)
                    procs[i] = (name, subprocess.Popen(
                        cmd, cwd=os.path.join(REPO_ROOT, "stateful")
                    ))
        time.sleep(3)


if __name__ == "__main__":
    main()
