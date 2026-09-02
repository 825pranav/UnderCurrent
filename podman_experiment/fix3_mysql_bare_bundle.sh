#!/bin/bash
# fix3_mysql_bare_bundle.sh — fully hand-built OCI bundle, driven entirely by
# bare runc (create/start/checkpoint/restore), with NO podman/conmon
# supervision at any point. Only uses podman to obtain the image's merged
# rootfs (`podman mount`) and inspect its Entrypoint/Cmd/Env — podman never
# starts the container itself.
#
# This is the full test of the theory from fix1b/fix2: if conmon/podman
# lifecycle supervision is what breaks restore, removing it entirely should
# let restore succeed.
#
# Must be run as root.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="mysql-podman-ckpt-exp"
IMAGE="docker.io/library/mysql@sha256:24e450bbd24f621c71b10404c946cc9ea1cbb0e6e7464b2be2de5193dcf1d05b"
CKPT_DIR="/tmp/mysql-ckpt-bare-bundle"
RUNC_ROOT="/tmp/runc-bare-bundle-root"
BUNDLE_DIR="/tmp/mysql-bare-bundle"
CID="mysql-bare-exp"

echo "=== Clean slate ==="
podman umount "$NAME" >/dev/null 2>&1
podman rm -f "$NAME" >/dev/null 2>&1
rm -rf "$CKPT_DIR" "$RUNC_ROOT" "$BUNDLE_DIR"
mkdir -p "$CKPT_DIR" "$RUNC_ROOT" "$BUNDLE_DIR/rootfs_placeholder"

echo "=== podman create (storage only, never started) ==="
podman create --name "$NAME" \
  -e MYSQL_ROOT_PASSWORD=exp_pass -e MYSQL_DATABASE=expdb "$IMAGE" >/dev/null
if [ $? -ne 0 ]; then echo "FAILED to create container"; exit 1; fi

echo "=== podman mount (get merged rootfs) ==="
ROOTFS=$(podman mount "$NAME")
echo "rootfs: $ROOTFS"
if [ -z "$ROOTFS" ]; then echo "FAILED to mount rootfs"; exit 1; fi

echo "=== Generating hand-built config.json ==="
python3 "$REPO_DIR/podman_experiment/gen_bundle_config.py" \
  "$IMAGE" "$ROOTFS" "$CID" "$BUNDLE_DIR/config.json" \
  "MYSQL_ROOT_PASSWORD=exp_pass" "MYSQL_DATABASE=expdb"
if [ ! -f "$BUNDLE_DIR/config.json" ]; then
  echo "FAILED to generate config.json"
  podman umount "$NAME" >/dev/null 2>&1; podman rm -f "$NAME" >/dev/null 2>&1
  exit 1
fi

echo "=== Loosening perms on VOLUME-declared paths (podman normally handles this at container-create; our hand-built bundle skips that machinery, and ownership fidelity doesn't matter for this throwaway test) ==="
mkdir -p "$ROOTFS/var/lib/mysql-files" "$ROOTFS/var/lib/mysql" "$ROOTFS/var/run/mysqld"
chmod -R 0777 "$ROOTFS/var/lib/mysql-files" "$ROOTFS/var/lib/mysql" "$ROOTFS/var/run/mysqld"

echo "=== Starting via BARE runc (isolated state root, no podman/conmon at all) ==="
runc --root "$RUNC_ROOT" run -d --bundle "$BUNDLE_DIR" "$CID"
RUN_RC=$?
echo "=== runc run exit code: $RUN_RC ==="
if [ $RUN_RC -ne 0 ]; then
  echo "FAILED to start via bare runc"
  podman umount "$NAME" >/dev/null 2>&1; podman rm -f "$NAME" >/dev/null 2>&1
  chown -R "${SUDO_USER:-$(logname)}":"${SUDO_USER:-$(logname)}" "$REPO_DIR/podman_experiment"
  exit 1
fi

runc --root "$RUNC_ROOT" list

echo "=== Waiting for mysqld to be ready (runc exec) ==="
READY=0
for i in $(seq 1 40); do
  if runc --root "$RUNC_ROOT" exec "$CID" mysqladmin ping -uroot -pexp_pass >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 1
done
echo "mysqld ready: $READY"
if [ "$READY" -ne 1 ]; then
  echo "Never became ready. Diagnostic exec output:"
  runc --root "$RUNC_ROOT" exec "$CID" mysqladmin ping -uroot -pexp_pass
  echo "--- last lines of mysql error log inside rootfs (if present) ---"
  tail -n 40 "$ROOTFS/var/lib/mysql/"*.err 2>/dev/null
  runc --root "$RUNC_ROOT" delete -f "$CID" >/dev/null 2>&1
  podman umount "$NAME" >/dev/null 2>&1; podman rm -f "$NAME" >/dev/null 2>&1
  chown -R "${SUDO_USER:-$(logname)}":"${SUDO_USER:-$(logname)}" "$REPO_DIR/podman_experiment"
  exit 1
fi

echo "=== Checkpoint (--file-locks) ==="
runc --root "$RUNC_ROOT" checkpoint \
  --image-path "$CKPT_DIR" --work-path "$CKPT_DIR" --file-locks "$CID"
CKPT_RC=$?
echo "=== checkpoint exit code: $CKPT_RC ==="
if [ $CKPT_RC -ne 0 ]; then
  echo "dump.log tail:"; tail -n 40 "$CKPT_DIR/dump.log" 2>/dev/null
  runc --root "$RUNC_ROOT" delete -f "$CID" >/dev/null 2>&1
  podman umount "$NAME" >/dev/null 2>&1; podman rm -f "$NAME" >/dev/null 2>&1
  chown -R "${SUDO_USER:-$(logname)}":"${SUDO_USER:-$(logname)}" "$REPO_DIR/podman_experiment"
  exit 1
fi

echo "=== runc list after checkpoint ==="
runc --root "$RUNC_ROOT" list

echo "=== Restore (--file-locks --detach) ==="
runc --root "$RUNC_ROOT" restore \
  --image-path "$CKPT_DIR" --work-path "$CKPT_DIR" \
  --bundle "$BUNDLE_DIR" --file-locks --detach "$CID"
RESTORE_RC=$?
echo "=== runc restore exit code: $RESTORE_RC ==="

if [ -f "$CKPT_DIR/restore.log" ]; then
  echo "=== restore.log tail ==="
  tail -n 60 "$CKPT_DIR/restore.log"
else
  echo "(no restore.log)"; ls -la "$CKPT_DIR"
fi

runc --root "$RUNC_ROOT" list

if [ $RESTORE_RC -eq 0 ]; then
  echo "=== Verifying mysql responds post-restore ==="
  sleep 3
  runc --root "$RUNC_ROOT" exec "$CID" mysqladmin ping -uroot -pexp_pass
  echo "ping exit code: $?"
fi

echo "=== Cleanup ==="
runc --root "$RUNC_ROOT" delete -f "$CID" >/dev/null 2>&1
podman umount "$NAME" >/dev/null 2>&1
podman rm -f "$NAME" >/dev/null 2>&1

ORIG_USER="${SUDO_USER:-$(logname)}"
chown -R "$ORIG_USER":"$ORIG_USER" "$REPO_DIR/podman_experiment"

if [ $RESTORE_RC -eq 0 ]; then
  echo "=== RESULT: mysql full checkpoint+restore, bare runc + hand-built bundle: SUCCESS ==="
else
  echo "=== RESULT: mysql restore, bare runc + hand-built bundle: FAILED ==="
fi
