#!/usr/bin/env bash
# *** DO NOT USE. Runs dd inside the container; comm "dd" is dropped by the
# *** eBPF comm filter so nothing is attributed. Use inject_fault_v2.sh.
# inject_fault.sh — Inject a VFS write fault into a running container.
#
# Usage:
#   ./inject_fault.sh <container> [duration_s]
#
# Method: writes to /dev/full inside the container (every write returns ENOSPC)
# for `duration_s` seconds, then exits. The container itself keeps running.
# The eBPF vfs_write_error probe in event_listener.py picks up the errors.
#
# If you're running in simulated mode (no eBPF), the fault is injected directly
# into the StatefulStateStore via the Python inject helper instead.
#
# Requirements: docker exec access to the target container.

set -euo pipefail

CONTAINER="${1:?Usage: inject_fault.sh <container> [duration_s]}"
DURATION="${2:-15}"

echo "[inject] starting VFS fault on ${CONTAINER} for ${DURATION}s"

# Write to /dev/full inside the container. dd will get ENOSPC on every write,
# generating vfs_write_error events visible to the eBPF probe. We suppress the
# exit error from dd since ENOSPC is expected.
docker exec "${CONTAINER}" bash -c "
  timeout ${DURATION} bash -c '
    while true; do
      dd if=/dev/zero of=/dev/full bs=4096 count=16 2>/dev/null || true
      sleep 0.1
    done
  ' || true
" &

FAULT_PID=$!
echo "[inject] fault running (bg pid ${FAULT_PID}) — will last ${DURATION}s"
wait "${FAULT_PID}" 2>/dev/null || true
echo "[inject] fault injection complete for ${CONTAINER}"
