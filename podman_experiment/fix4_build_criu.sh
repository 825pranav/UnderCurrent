#!/bin/bash
# fix4_build_criu.sh — build CRIU from source against this exact kernel
# (6.8.0-136-generic), replacing the apt-packaged 3.16.1 (released ~2021).
# Theory (REPORT.md): postgres/redis restore fail at the CRIU layer in ways
# consistent with CRIU/kernel version skew, not a Docker/Podman defect.
#
# Installs to /usr/local/sbin (CRIU's default PREFIX), which precedes
# /usr/sbin in root's PATH on Ubuntu, so it takes over from the apt package
# without removing it (apt package stays installed, just shadowed).
#
# Must be run as root. Build tree lives in /tmp/criu-src (throwaway).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="/tmp/criu-src"

echo "=== Build dependencies (apt) ==="
apt-get update -qq
apt-get install -y -qq \
  build-essential pkg-config libprotobuf-dev libprotobuf-c-dev \
  protobuf-c-compiler protobuf-compiler python3-protobuf \
  libcap-dev libnl-3-dev libnet1-dev libbsd-dev

echo "=== Current criu (apt-packaged) ==="
which criu && criu --version

echo "=== Cloning checkpoint-restore/criu ==="
rm -rf "$SRC_DIR"
git clone --depth 100 https://github.com/checkpoint-restore/criu.git "$SRC_DIR"
cd "$SRC_DIR"
LATEST_TAG=$(git tag --sort=-v:refname | head -1)
echo "=== Latest tag: $LATEST_TAG — building this (not master, for reproducibility) ==="
git checkout "$LATEST_TAG"

echo "=== Building (make -j\$(nproc)) ==="
make -j"$(nproc)"

echo "=== Installing (make install, PREFIX=/usr/local by default) ==="
make install

echo "=== New criu resolved via PATH ==="
hash -r
which criu
criu --version

echo "=== Restoring ownership of repo files (none touched, but keep consistent) ==="
chown -R "${SUDO_USER:-$(logname)}":"${SUDO_USER:-$(logname)}" "$REPO_DIR/podman_experiment"

echo "=== Done. Old apt-packaged criu is untouched at /usr/sbin/criu (dpkg still tracks it); new build shadows it via /usr/local/sbin ==="
