#!/bin/bash
# rootful_run.sh — run the Podman checkpoint/restore experiment under
# rootful podman (rootless podman 3.4.4 hard-refuses checkpoint/restore
# with "requires root"). Must be invoked with sudo/as root.
#
# Only touches: root's podman image/container storage (separate from the
# user's rootless podman storage and from Docker entirely), and files under
# podman_experiment/. Does not touch docker-compose.yml, pgdata, mysqldata,
# or any committed data/results.

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXP_DIR="$REPO_DIR/podman_experiment"

echo "=== Pulling pinned digests into root podman storage ==="
podman pull docker.io/library/postgres@sha256:52e6ffd11fddd081ae63880b635b2a61c14008c17fc98cdc7ce5472265516dd0
podman pull docker.io/library/redis@sha256:1f073813b641755b70b0200da64131bbeeb4ec5b633ca67772229b49820caafa
podman pull docker.io/library/mysql@sha256:24e450bbd24f621c71b10404c946cc9ea1cbb0e6e7464b2be2de5193dcf1d05b

echo "=== Running experiment as root ==="
python3 "$EXP_DIR/run_experiment.py"

echo "=== Restoring file ownership to original user ==="
ORIG_USER="${SUDO_USER:-$(logname)}"
chown -R "$ORIG_USER":"$ORIG_USER" "$EXP_DIR"

echo "=== Done ==="
