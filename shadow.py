#!/usr/bin/env python3
# shadow.py — UnderCurrent shadow-controller launcher
#
# Starts both control-plane pipelines in SHADOW (dry-run) mode:
#   decisions are computed and logged, but NO real actions are executed.
#   The FSM uses a fresh throwaway instance each cycle so the live state
#   is never mutated.
#
# This is the safe way to observe what UnderCurrent *would* do without
# touching any running containers or workloads.
#
# Usage:
#   python3 shadow.py                    # both tracks, React dashboard (http://localhost:5050)
#   python3 shadow.py --real             # both tracks, live eBPF (root required)
#   python3 shadow.py --track stateless  # Type-S shadow only
#   python3 shadow.py --track stateful   # Type-F shadow only
#   python3 shadow.py --interval 10      # reconcile every 10 s
#   python3 shadow.py --no-dashboard     # skip the dashboard
#   python3 shadow.py --streamlit        # use Streamlit dashboard instead
#
# To run the real (active) controller instead:
#   python3 run.py

import os
import subprocess
import sys

launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integration", "launcher.py")
sys.exit(subprocess.run([sys.executable, launcher, "--mode", "shadow"] + sys.argv[1:]).returncode)
