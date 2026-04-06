#!/usr/bin/env python3
# run.py — UnderCurrent real-mode launcher (normal entry point)
#
# Starts both control-plane pipelines in REAL mode:
#   real actions execute, FSM advances, audit logs are written.
#
# Usage:
#   python3 run.py                    # both tracks, React dashboard (http://localhost:5050)
#   python3 run.py --real             # both tracks, live eBPF (root required)
#   python3 run.py --track stateless  # Type-S only
#   python3 run.py --track stateful   # Type-F only
#   python3 run.py --interval 10      # reconcile every 10 s
#   python3 run.py --no-dashboard     # skip the dashboard
#   python3 run.py --streamlit        # use Streamlit dashboard instead
#
# To run the shadow controller instead:
#   python3 shadow.py
#
# To open just the Streamlit dashboard:
#   python3 streamlit_dash.py

import os
import sys

# Delegate entirely to the unified launcher with --mode real (the default).
launcher = os.path.join(os.path.dirname(__file__), "integration", "launcher.py")
sys.argv = [launcher, "--mode", "real"] + sys.argv[1:]

exec(open(launcher).read(), {"__name__": "__main__", "__file__": launcher})
