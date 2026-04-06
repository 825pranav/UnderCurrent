#!/usr/bin/env python3
# streamlit_dash.py — Launch the Streamlit unified dashboard
#
# Opens the Streamlit dashboard at http://localhost:8501
# (both Type-S and Type-F traces, no pipelines started)
#
# Usage:
#   python3 streamlit_dash.py              # port 8501 (default)
#   python3 streamlit_dash.py --port 8502  # custom port
#
# To launch the React dashboard (default with run.py):
#   python3 run.py                         # http://localhost:5050

import os
import subprocess
import sys

REPO_ROOT      = os.path.dirname(os.path.abspath(__file__))
STREAMLIT_DASH = os.path.join(REPO_ROOT, "integration", "unified_dashboard.py")

port = "8501"
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == "--port" and i < len(sys.argv) - 1:
        port = sys.argv[i + 1]

if not os.path.exists(STREAMLIT_DASH):
    print(f"ERROR: dashboard not found at {STREAMLIT_DASH}")
    sys.exit(1)

cmd = ["streamlit", "run", STREAMLIT_DASH, "--server.port", port]
print(f"Starting Streamlit dashboard → http://localhost:{port}")
subprocess.run(cmd, cwd=REPO_ROOT)
