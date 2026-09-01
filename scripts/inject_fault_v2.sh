#!/usr/bin/env bash
# inject_fault_v2.sh — inject faults the eBPF layer can actually attribute.
#
# WHY THIS EXISTS
#   inject_fault.sh runs `dd` inside the container. The eBPF listeners map
#   events to containers by the task's `comm` (see _DOCKER_COMM_MAP), which
#   only contains nginx / postgres / redis-server / mysqld. A `dd` process has
#   comm "dd", so every event it generates is dropped before the StateStore.
#   Verified 2026-09-01: injecting into redis and mysql with that script
#   produced ZERO records, while postgres showed only its own idle activity.
#
#   Everything below makes the SERVER PROCESS ITSELF perform the failing or
#   slow I/O, so comm matches and the event is attributed.
#
# Usage: ./inject_fault_v2.sh <container> [duration_s]

set -uo pipefail
C="${1:?Usage: inject_fault_v2.sh <container> [duration_s]}"
D="${2:-30}"
END=$(( $(date +%s) + D ))
echo "[inject-v2] ${C} for ${D}s"

case "$C" in
  postgres)
    # postgres backend writes to /dev/full -> ENOSPC on every write.
    # comm = "postgres". Produces vfs_write_error.
    while [ "$(date +%s)" -lt "$END" ]; do
      docker exec "$C" psql -U postgres -d postgres -q \
        -c "COPY (SELECT generate_series(1,400000)) TO '/dev/full';" >/dev/null 2>&1
    done
    ;;

  mysql)
    # mysqld does synchronous InnoDB writes (flush_log_at_trx_commit=1,
    # O_DIRECT). Each commit is a real disk write from comm = "mysqld".
    docker exec "$C" mysql -uroot -ppass -e \
      "CREATE DATABASE IF NOT EXISTS uc; USE uc;
       CREATE TABLE IF NOT EXISTS uc_load (id INT AUTO_INCREMENT PRIMARY KEY, p BLOB);" >/dev/null 2>&1
    while [ "$(date +%s)" -lt "$END" ]; do
      docker exec "$C" mysql -uroot -ppass uc -e \
        "INSERT INTO uc_load (p) SELECT REPEAT('x',60000) FROM information_schema.columns LIMIT 200;
         FLUSH TABLES;" >/dev/null 2>&1
    done
    ;;

  redis)
    # redis-server writes the RDB itself during SAVE (synchronous, blocking).
    # comm = "redis-server". BGSAVE's fork keeps the same comm.
    while [ "$(date +%s)" -lt "$END" ]; do
      docker exec "$C" redis-benchmark -t set -n 20000 -d 4096 -q >/dev/null 2>&1
      docker exec "$C" redis-cli SAVE >/dev/null 2>&1
    done
    ;;

  nginx)
    # Type-S signal: kill a worker so the tracepoint sees comm = "nginx".
    while [ "$(date +%s)" -lt "$END" ]; do
      docker exec "$C" sh -c 'pkill -o -x nginx' >/dev/null 2>&1
      sleep 3
    done
    ;;

  *) echo "[inject-v2] no method for '$C'"; exit 1 ;;
esac
echo "[inject-v2] complete: ${C}"
