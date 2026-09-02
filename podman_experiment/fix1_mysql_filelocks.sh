#!/bin/bash
# fix1_mysql_filelocks.sh — bypass Docker/Podman's checkpoint CLI entirely and
# drive runc directly with --file-locks, the flag neither Docker's nor
# Podman's CLI exposes. mysqld holds POSIX file locks; CRIU refuses to dump
# a process holding them unless told --file-locks.
#
# Root cause reference: podman_experiment/REPORT.md, "mysql" row.
# Must be run as root (sudo). Only touches: root's podman storage (already
# used by the earlier experiment), /tmp/mysql-ckpt-runc, and this directory.
# Does not touch docker-compose.yml, pgdata, mysqldata, or data/phase2/.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="mysql-podman-ckpt-exp"
IMAGE="docker.io/library/mysql@sha256:24e450bbd24f621c71b10404c946cc9ea1cbb0e6e7464b2be2de5193dcf1d05b"
CKPT_DIR="/tmp/mysql-ckpt-runc"

echo "=== Clean slate ==="
podman rm -f "$NAME" >/dev/null 2>&1
rm -rf "$CKPT_DIR"
mkdir -p "$CKPT_DIR"

echo "=== Starting fresh mysql container (podman, runtime=runc) ==="
podman run -d --runtime runc --name "$NAME" \
  -e MYSQL_ROOT_PASSWORD=exp_pass -e MYSQL_DATABASE=expdb "$IMAGE"
if [ $? -ne 0 ]; then
  echo "FAILED to start container"; exit 1
fi

echo "=== Waiting for mysql to report Running ==="
for i in $(seq 1 30); do
  running=$(podman inspect --format '{{.State.Running}}' "$NAME" 2>/dev/null)
  if [ "$running" = "true" ]; then break; fi
  sleep 1
done
sleep 5  # let mysqld finish booting inside

CID=$(podman inspect --format '{{.Id}}' "$NAME")
echo "=== Podman container id: $CID ==="

echo "=== Locating runc state root (searching /run) ==="
STATE_FILE=$(find /run -maxdepth 8 -name state.json 2>/dev/null | xargs grep -l "\"id\":\"$CID\"" 2>/dev/null | head -1)
if [ -z "$STATE_FILE" ]; then
  echo "Could not find state.json for $CID under /run by exact id match; trying broader search"
  STATE_FILE=$(find /run -maxdepth 8 -type d -name "$CID" 2>/dev/null | head -1)/state.json
fi
if [ ! -f "$STATE_FILE" ]; then
  echo "FAILED: could not locate runc state.json for container $CID"
  echo "Debug: candidate dirs under /run:"
  find /run -maxdepth 4 -type d 2>/dev/null | grep -iE 'runc|runtime|container'
  exit 1
fi
RUNC_ROOT="$(dirname "$(dirname "$STATE_FILE")")"
echo "=== runc state root: $RUNC_ROOT ==="
echo "=== runc list (this root) ==="
runc --root "$RUNC_ROOT" list

echo "=== Attempting runc checkpoint --file-locks ==="
runc --root "$RUNC_ROOT" checkpoint \
  --image-path "$CKPT_DIR" \
  --work-path "$CKPT_DIR" \
  --file-locks \
  "$CID"
RC=$?
echo "=== runc checkpoint exit code: $RC ==="

echo "=== dump.log tail (if present) ==="
if [ -f "$CKPT_DIR/dump.log" ]; then
  tail -n 60 "$CKPT_DIR/dump.log"
else
  echo "(no dump.log found in $CKPT_DIR)"
  ls -la "$CKPT_DIR"
fi

echo "=== Cleanup ==="
podman rm -f "$NAME" >/dev/null 2>&1

echo "=== Restoring ownership of repo files touched ==="
ORIG_USER="${SUDO_USER:-$(logname)}"
chown -R "$ORIG_USER":"$ORIG_USER" "$REPO_DIR/podman_experiment"

if [ $RC -eq 0 ]; then
  echo "=== RESULT: mysql checkpoint via raw runc --file-locks: SUCCESS ==="
else
  echo "=== RESULT: mysql checkpoint via raw runc --file-locks: FAILED (see dump.log above) ==="
fi
