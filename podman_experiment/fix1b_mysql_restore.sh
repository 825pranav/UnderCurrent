#!/bin/bash
# fix1b_mysql_restore.sh — follow-on to fix1: after confirming raw runc
# checkpoint --file-locks succeeds for mysql, test whether raw runc restore
# from that checkpoint also succeeds (full checkpoint+restore cycle, still
# bypassing Docker/Podman's CLI). Must be run as root.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="mysql-podman-ckpt-exp"
IMAGE="docker.io/library/mysql@sha256:24e450bbd24f621c71b10404c946cc9ea1cbb0e6e7464b2be2de5193dcf1d05b"
CKPT_DIR="/tmp/mysql-ckpt-runc-restore"

echo "=== Clean slate ==="
podman rm -f "$NAME" >/dev/null 2>&1
rm -rf "$CKPT_DIR"
mkdir -p "$CKPT_DIR"

echo "=== Starting fresh mysql container (podman, runtime=runc) ==="
podman run -d --runtime runc --name "$NAME" \
  -e MYSQL_ROOT_PASSWORD=exp_pass -e MYSQL_DATABASE=expdb "$IMAGE"
if [ $? -ne 0 ]; then echo "FAILED to start container"; exit 1; fi

echo "=== Waiting for mysql to report Running ==="
for i in $(seq 1 30); do
  running=$(podman inspect --format '{{.State.Running}}' "$NAME" 2>/dev/null)
  if [ "$running" = "true" ]; then break; fi
  sleep 1
done
sleep 5

CID=$(podman inspect --format '{{.Id}}' "$NAME")
echo "=== Podman container id: $CID ==="

STATE_FILE=$(find /run -maxdepth 8 -name state.json 2>/dev/null | xargs grep -l "\"id\":\"$CID\"" 2>/dev/null | head -1)
RUNC_ROOT="$(dirname "$(dirname "$STATE_FILE")")"
BUNDLE=$(runc --root "$RUNC_ROOT" list | awk -v id="$CID" '$1==id {print $4}')
echo "=== runc root: $RUNC_ROOT ==="
echo "=== bundle: $BUNDLE ==="

echo "=== Checkpoint (--file-locks, leaves container stopped) ==="
runc --root "$RUNC_ROOT" checkpoint \
  --image-path "$CKPT_DIR" --work-path "$CKPT_DIR" --file-locks "$CID"
CKPT_RC=$?
echo "=== checkpoint exit code: $CKPT_RC ==="
if [ $CKPT_RC -ne 0 ]; then
  echo "Checkpoint failed, cannot test restore. dump.log tail:"
  tail -n 40 "$CKPT_DIR/dump.log" 2>/dev/null
  podman rm -f "$NAME" >/dev/null 2>&1
  chown -R "${SUDO_USER:-$(logname)}":"${SUDO_USER:-$(logname)}" "$REPO_DIR/podman_experiment"
  exit 1
fi

echo "=== runc list after checkpoint (container should be stopped, not removed) ==="
runc --root "$RUNC_ROOT" list

echo "=== Attempting runc restore --file-locks --detach ==="
runc --root "$RUNC_ROOT" restore \
  --image-path "$CKPT_DIR" --work-path "$CKPT_DIR" \
  --bundle "$BUNDLE" --file-locks --detach "$CID"
RESTORE_RC=$?
echo "=== runc restore exit code: $RESTORE_RC ==="

echo "=== restore.log tail (if present) ==="
if [ -f "$CKPT_DIR/restore.log" ]; then
  tail -n 60 "$CKPT_DIR/restore.log"
else
  echo "(no restore.log found in $CKPT_DIR)"
  ls -la "$CKPT_DIR"
fi

echo "=== runc list after restore attempt ==="
runc --root "$RUNC_ROOT" list

if [ $RESTORE_RC -eq 0 ]; then
  echo "=== Checking mysql actually responds post-restore ==="
  sleep 3
  podman exec "$NAME" mysqladmin ping -uroot -pexp_pass 2>&1 || echo "(mysqladmin ping failed or podman lost track of container state)"
fi

echo "=== Cleanup ==="
podman rm -f "$NAME" >/dev/null 2>&1
runc --root "$RUNC_ROOT" delete -f "$CID" >/dev/null 2>&1

echo "=== Restoring ownership of repo files touched ==="
ORIG_USER="${SUDO_USER:-$(logname)}"
chown -R "$ORIG_USER":"$ORIG_USER" "$REPO_DIR/podman_experiment"

if [ $RESTORE_RC -eq 0 ]; then
  echo "=== RESULT: mysql checkpoint+restore via raw runc --file-locks: SUCCESS ==="
else
  echo "=== RESULT: mysql restore via raw runc --file-locks: FAILED (see restore.log above) ==="
fi
