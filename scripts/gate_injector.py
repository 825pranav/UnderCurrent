#!/usr/bin/env python3
"""
gate_injector.py — HANDOFF_v3 Step 2 gate.

Precondition: the controller is running (run.py --real, under the PYTHONPATH
wrapper, as root).  For each container this script runs inject_fault_v2.sh for
INJECT_S seconds, waits one extra window, then scans the live trace files for
records of that container written after injection began whose score crosses
τ_flush (0.50).  All four must pass before the long run starts.

Also asserts the controller log contains no WASM-FALLBACK line (trap A).
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INJECT_S = int(os.environ.get("INJECT_S", "60"))
SETTLE_S = int(os.environ.get("SETTLE_S", "20"))
TAU_FLUSH = 0.50
CONTROLLER_LOG = os.environ.get("UC_LOG", "/tmp/uc_rerun.log")
TRACES = {
    "F": os.path.join(ROOT, "stateful", "traces.jsonl"),
    "S": os.path.join(ROOT, "stateless", "traces.jsonl"),
}
TRACK = {"postgres": "F", "redis": "F", "mysql": "F", "nginx": "S"}


def records_since(path, t0):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("trace_time", 0) >= t0:
                out.append(r)
    return out


def controller_alive():
    p = subprocess.run(["pgrep", "-f", "run.py --real"], capture_output=True, text=True)
    return p.returncode == 0


results = {}
if not controller_alive():
    print("GATE: FAIL — controller (run.py --real) is not running")
    sys.exit(2)

for c in ["postgres", "redis", "mysql", "nginx"]:
    t0 = time.time()
    print(f"[gate] injecting {c} for {INJECT_S}s …", flush=True)
    subprocess.run([os.path.join(ROOT, "scripts", "inject_fault_v2.sh"), c, str(INJECT_S)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(SETTLE_S)
    recs = [r for r in records_since(TRACES[TRACK[c]], t0) if r.get("container") == c]
    real = [r for r in recs if r.get("mode") == "real"]
    top = max((r.get("score", 0) for r in real), default=0.0)
    actions = sorted({r.get("action") for r in real})
    sigs = sorted({s for r in real for s in r.get("kernel_signals", [])})
    results[c] = {
        "track": TRACK[c], "records_real": len(real), "max_score": top,
        "actions": actions, "kernel_signals": sigs,
        "pass": len(real) > 0 and top >= TAU_FLUSH,
    }
    print(f"[gate] {c}: n={len(real)} max_score={top} actions={actions} signals={sigs} "
          f"→ {'PASS' if results[c]['pass'] else 'FAIL'}", flush=True)

wasm_fallback = 0
if os.path.exists(CONTROLLER_LOG):
    with open(CONTROLLER_LOG, errors="replace") as fh:
        wasm_fallback = sum(1 for l in fh if "WASM-FALLBACK" in l)
results["_wasm_fallback_lines"] = wasm_fallback
results["_meta"] = {"date": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "inject_s": INJECT_S}

out = os.path.join(ROOT, "evaluation", "results", "injector_gate.json")
with open(out, "w") as fh:
    json.dump(results, fh, indent=2)
ok = all(v["pass"] for k, v in results.items() if not k.startswith("_")) and wasm_fallback == 0
print(json.dumps(results, indent=2))
print("GATE:", "PASS" if ok else "FAIL", f"(WASM-FALLBACK lines: {wasm_fallback})")
sys.exit(0 if ok else 1)
