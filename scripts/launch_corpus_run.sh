#!/usr/bin/env bash
# launch_corpus_run.sh — HANDOFF_v3 step 3, in one place.
#
#   0. wait for any running MTTR campaign (run_episodes.py) to finish —
#      its docker restarts would pollute the corpus
#   1. archive the previous traces (trap D — the logger appends)
#   2. restart the four containers (trap F — never compose up/down)
#   3. start the controller under the PYTHONPATH wrapper (trap A) with nohup
#      AFTER a foreground `sudo -v` (trap E); keep the sudo ticket alive in
#      the background so the campaign's io.max writes keep working for hours
#   4. verify zero WASM-FALLBACK lines
#   5. run gate_injector.py (step 2 gate, ~6 min) — abort unless all four pass
#   6. start the injection campaign
#
# Usage (in a real terminal on the VM, not backgrounded):
#   sudo -v && nohup ./scripts/launch_corpus_run.sh 20 > /tmp/uc_launch.log 2>&1 &
set -uo pipefail
H="${1:?Usage: launch_corpus_run.sh <hours>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PP=/home/pes2ug23cs429/.local/lib/python3.10/site-packages
LOG=/tmp/uc_rerun.log

sudo -n true 2>/dev/null || { echo "run 'sudo -v' first (trap E)"; exit 1; }
pgrep -f "run.py --real" >/dev/null && { echo "controller already running"; exit 1; }

# keep the sudo ticket alive for the whole run (refresh needs no password
# while the ticket is valid); dies with this script's process group.
( while true; do sudo -n -v 2>/dev/null || exit; sleep 60; done ) &
KEEPALIVE=$!
echo "[launch] sudo keepalive pid $KEEPALIVE"

while pgrep -f "run_episodes.py" >/dev/null; do
  echo "[launch] $(date +%H:%M:%S) waiting for MTTR episodes to finish…"; sleep 60
done

A="archive/run-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$A"
for f in stateful/traces.jsonl stateless/traces.jsonl stateful/divergence_log.jsonl; do
  [ -f "$f" ] && mv "$f" "$A/$(echo $f | tr / _)"
done
echo "[launch] previous traces → $A"

for c in postgres redis mysql nginx; do docker restart "$c" >/dev/null; done
sleep 15
docker ps --format '{{.Names}} {{.Status}}'

nohup sudo env PYTHONPATH=$PP python3 run.py --real --no-dashboard > "$LOG" 2>&1 &
echo "[launch] controller pid $!  log $LOG"
sleep 30
n=$(grep -ci "WASM-FALLBACK" "$LOG"); echo "[launch] WASM-FALLBACK lines: $n"
[ "$n" -eq 0 ] || { echo "ABORT: WASM fallback active (trap A)"; exit 1; }
pgrep -f "run.py --real" >/dev/null || { echo "ABORT: controller died"; tail -20 "$LOG"; exit 1; }

echo "[launch] running step-2 gate (injects each container for 60 s)…"
if PYTHONPATH=$PP python3 scripts/gate_injector.py > /tmp/uc_gate.log 2>&1; then
  echo "[launch] GATE PASS — $(grep -o '"max_score": [0-9.]*' /tmp/uc_gate.log | tr '\n' ' ')"
else
  echo "ABORT: step-2 gate failed; see /tmp/uc_gate.log and evaluation/results/injector_gate.json"
  tail -15 /tmp/uc_gate.log; exit 1
fi

nohup ./scripts/fault_campaign.sh "$H" > /tmp/uc_campaign.nohup 2>&1 &
echo "[launch] campaign pid $! for ${H}h; log /tmp/uc_campaign.log"
echo "[launch] controller log /tmp/uc_rerun.log; traces stateful/traces.jsonl stateless/traces.jsonl"
wait $!
echo "[launch] campaign finished $(date -Is); controller still running — stop with: sudo pkill -f 'run.py --real'"
