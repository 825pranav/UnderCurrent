#!/bin/bash
# fix2_mysql_raw_runc_full.sh — go one level deeper than fix1b: fully detach
# the container from Podman/conmon supervision so nothing races the restore.
#
# fix1b showed that even though `runc checkpoint --file-locks` succeeds,
# `runc restore` afterward fails with "Can't open file dev/null on restore"
# — most likely because conmon (which is still supervising the process,
# since it was started via `podman run`) sees the checkpoint's kill of
# mysqld as the container exiting, and starts tearing down container state
# (device/mount setup) before we call `runc restore` on the same bundle.
#
# This script tests that theory directly: use `podman create` (which builds
# the OCI bundle/config.json and storage but does NOT start the container or
# attach conmon), then drive the entire lifecycle — start, checkpoint,
# restore — via bare `runc` with its own isolated state root, so conmon is
# never in the picture and nothing else can race the restore.
#
# Must be run as root. Only touches root's podman storage + /tmp scratch dirs.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="mysql-podman-ckpt-exp"
IMAGE="docker.io/library/mysql@sha256:24e450bbd24f621c71b10404c946cc9ea1cbb0e6e7464b2be2de5193dcf1d05b"
CKPT_DIR="/tmp/mysql-ckpt-raw-runc"
RUNC_ROOT="/tmp/runc-manual-root"

echo "=== Clean slate ==="
podman rm -f "$NAME" >/dev/null 2>&1
rm -rf "$CKPT_DIR" "$RUNC_ROOT"
mkdir -p "$CKPT_DIR" "$RUNC_ROOT"

echo "=== podman create (builds bundle/storage, does NOT start / attach conmon) ==="
podman create --runtime runc --name "$NAME" \
  -e MYSQL_ROOT_PASSWORD=exp_pass -e MYSQL_DATABASE=expdb "$IMAGE"
if [ $? -ne 0 ]; then echo "FAILED to create container"; exit 1; fi

CID=$(podman inspect --format '{{.Id}}' "$NAME")
BUNDLE="/var/lib/containers/storage/overlay-containers/$CID/userdata"
echo "=== CID: $CID ==="
echo "=== bundle: $BUNDLE ==="

echo "=== Checking config.json for hooks / network namespace assumptions ==="
python3 -c "
import json
c = json.load(open('$BUNDLE/config.json'))
print('hooks:', c.get('hooks'))
for ns in c.get('linux', {}).get('namespaces', []):
    if ns.get('type') == 'network':
        print('network namespace entry:', ns)
"

echo "=== Starting container via BARE runc (own state root, no podman/conmon) ==="
runc --root "$RUNC_ROOT" run -d --bundle "$BUNDLE" "$CID"
RUN_RC=$?
echo "=== runc run exit code: $RUN_RC ==="
if [ $RUN_RC -ne 0 ]; then
  echo "FAILED to start via bare runc — this likely means the config.json has a\ndependency on podman-managed setup (e.g. a pre-created network namespace\npath) that podman create alone doesn't provide."
  podman rm -f "$NAME" >/dev/null 2>&1
  chown -R "${SUDO_USER:-$(logname)}":"${SUDO_USER:-$(logname)}" "$REPO_DIR/podman_experiment"
  exit 1
fi

echo "=== runc list ==="
runc --root "$RUNC_ROOT" list

echo "=== Waiting for mysqld to be ready (via runc exec) ==="
READY=0
for i in $(seq 1 30); do
  if runc --root "$RUNC_ROOT" exec "$CID" mysqladmin ping -uroot -pexp_pass >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 1
done
echo "mysqld ready: $READY"
if [ "$READY" -ne 1 ]; then
  echo "mysqld never became ready inside the bare-runc container; dumping runc exec error:"
  runc --root "$RUNC_ROOT" exec "$CID" mysqladmin ping -uroot -pexp_pass
fi

echo "=== Checkpoint (--file-locks, leaves stopped) ==="
runc --root "$RUNC_ROOT" checkpoint \
  --image-path "$CKPT_DIR" --work-path "$CKPT_DIR" --file-locks "$CID"
CKPT_RC=$?
echo "=== checkpoint exit code: $CKPT_RC ==="
if [ $CKPT_RC -ne 0 ]; then
  echo "dump.log tail:"; tail -n 40 "$CKPT_DIR/dump.log" 2>/dev/null
  runc --root "$RUNC_ROOT" delete -f "$CID" >/dev/null 2>&1
  podman rm -f "$NAME" >/dev/null 2>&1
  chown -R "${SUDO_USER:-$(logname)}":"${SUDO_USER:-$(logname)}" "$REPO_DIR/podman_experiment"
  exit 1
fi

echo "=== runc list after checkpoint (is state preserved, unlike the podman/conmon case?) ==="
runc --root "$RUNC_ROOT" list

echo "=== Restore (--file-locks --detach) ==="
runc --root "$RUNC_ROOT" restore \
  --image-path "$CKPT_DIR" --work-path "$CKPT_DIR" \
  --bundle "$BUNDLE" --file-locks --detach "$CID"
RESTORE_RC=$?
echo "=== runc restore exit code: $RESTORE_RC ==="

echo "=== restore.log tail (if present) ==="
if [ -f "$CKPT_DIR/restore.log" ]; then
  tail -n 60 "$CKPT_DIR/restore.log"
else
  echo "(no restore.log found)"; ls -la "$CKPT_DIR"
fi

echo "=== runc list after restore ==="
runc --root "$RUNC_ROOT" list

if [ $RESTORE_RC -eq 0 ]; then
  echo "=== Verifying mysql actually responds post-restore ==="
  sleep 3
  runc --root "$RUNC_ROOT" exec "$CID" mysqladmin ping -uroot -pexp_pass
  echo "ping exit code: $?"
fi

echo "=== Cleanup ==="
runc --root "$RUNC_ROOT" delete -f "$CID" >/dev/null 2>&1
podman rm -f "$NAME" >/dev/null 2>&1

echo "=== Restoring ownership of repo files touched ==="
ORIG_USER="${SUDO_USER:-$(logname)}"
chown -R "$ORIG_USER":"$ORIG_USER" "$REPO_DIR/podman_experiment"

if [ $RESTORE_RC -eq 0 ]; then
  echo "=== RESULT: mysql full checkpoint+restore via bare runc (no podman/conmon): SUCCESS ==="
else
  echo "=== RESULT: mysql restore via bare runc (no podman/conmon): FAILED (see restore.log above) ==="
fi
