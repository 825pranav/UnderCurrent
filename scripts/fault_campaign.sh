#!/usr/bin/env bash
# fault_campaign.sh — rotate inject_fault_v2.sh across all four containers for
# the length of a corpus run (HANDOFF_v3 step 3: "inject into all four on a
# rotation across the window, not once at the start").
#
# Usage: ./fault_campaign.sh <total_hours> [inject_s] [rest_s]
#   default: 60 s of injection per container, 120 s rest between containers.
#   One full cycle ≈ 4×(60+120) = 12 min → ~100 cycles in 20 h.
#
# Run in the foreground of a tmux/screen pane, or with nohup AFTER `sudo -v`.
set -uo pipefail
H="${1:?Usage: fault_campaign.sh <total_hours> [inject_s] [rest_s]}"
INJ="${2:-60}"; REST="${3:-120}"
HERE="$(cd "$(dirname "$0")" && pwd)"
END=$(( $(date +%s) + H*3600 ))
LOG="${CAMPAIGN_LOG:-/tmp/uc_campaign.log}"
cycle=0
echo "[campaign] start $(date -Is) for ${H}h; inject=${INJ}s rest=${REST}s; log=$LOG" | tee -a "$LOG"
while [ "$(date +%s)" -lt "$END" ]; do
  cycle=$((cycle+1))
  for c in postgres redis mysql nginx; do
    [ "$(date +%s)" -ge "$END" ] && break
    sudo -n -v 2>/dev/null || echo "[campaign] WARN sudo ticket expired; io.max throttle will be skipped" | tee -a "$LOG"
    echo "[campaign] cycle $cycle → $c $(date +%H:%M:%S) free=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)G" | tee -a "$LOG"
    "$HERE/inject_fault_v2.sh" "$c" "$INJ" >>"$LOG" 2>&1
    sleep "$REST"
  done
done
echo "[campaign] done $(date -Is) after $cycle cycles" | tee -a "$LOG"
