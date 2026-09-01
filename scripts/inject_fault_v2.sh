#!/usr/bin/env bash
# inject_fault_v2.sh — bounded fault injection the eBPF layer can attribute.
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
# BOUNDS (the previous revision of this file filled the disk — HANDOFF_v3 trap C)
#   * every loop checks free space and aborts below MIN_FREE_GB
#   * mysql inserts are capped at MYSQL_MAX_ROWS and the table is dropped on exit
#   * redis keyspace is capped at REDIS_KEYS and FLUSHALL'd on exit
#   * the host I/O-hog file is capped at HOG_MB and always removed on exit
#
# METHOD PER CONTAINER
#   postgres  backend COPY ... TO '/dev/full'  → ENOSPC from comm "postgres"
#             signal: vfs_write_error  (3 in window → 0.88 ≥ τ_repair)
#   redis     redis-server SAVE of an ~50 MB RDB every second while the same
#             host-side direct-I/O hog as mysql saturates the device → slow
#             bios issued by comm "redis-server"; signal: blk_io_latency ≥ 10 ms
#             (5 in window → 0.85 ≥ τ_repair). SAVE alone is not enough on a
#             fast virtio disk: measured 2026-09-01, every bio < 0.4 ms.
#   mysql     mysqld synchronous commits (flush_log_at_trx_commit=1, O_DIRECT)
#             while a host-side direct-I/O hog (dd, comm "dd" → dropped by the
#             filter) saturates the same device: the noisy-neighbour fault.
#             mysqld's own bios (threads ib_io_wr-N / ib_log_* / connection)
#             queue behind it → blk_io_latency ≥ 10 ms attributed to mysql.
#             NOTE: a cgroup io.max throttle does NOT work here — it delays
#             bios before the block layer, so the probes never see latency.
#   nginx     Type-S: kill one worker so sched_process_exit sees comm "nginx";
#             the master (PID 1) respawns it.
#
# Usage: ./inject_fault_v2.sh <container> [duration_s]
# Needs: docker group for docker exec. No sudo.

set -uo pipefail
C="${1:?Usage: inject_fault_v2.sh <container> [duration_s]}"
D="${2:-30}"
END=$(( $(date +%s) + D ))

MIN_FREE_GB="${MIN_FREE_GB:-8}"
HOG_FILE="${HOG_FILE:-/var/tmp/uc_iohog}"  # host-side direct-I/O hog (mysql fault)
HOG_MB="${HOG_MB:-1024}"                    # rewritten in place; never grows past this
MYSQL_MAX_ROWS="${MYSQL_MAX_ROWS:-3000}"    # 3000 × 6 KB ≈ 18 MB (+ binlog copy) ceiling
REDIS_KEYS="${REDIS_KEYS:-20000}"           # 20000 × 4 KB ≈ 80 MB RDB ceiling

log(){ echo "[inject-v2] $(date +%H:%M:%S) $*"; }

free_gb(){ df -BG --output=avail / | tail -1 | tr -dc '0-9'; }
check_disk(){
  local f; f=$(free_gb)
  if [ "$f" -lt "$MIN_FREE_GB" ]; then
    log "ABORT: only ${f} GB free (< ${MIN_FREE_GB} GB)"; return 1
  fi
}

# The controller usually restarts the target container right at the end of a
# slot (C&R), so cleanup must wait for it to accept connections again.
wait_mysql(){ for i in $(seq 1 45); do docker exec mysql mysqladmin -uroot -ppass ping >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
wait_redis(){ for i in $(seq 1 45); do [ "$(docker exec redis redis-cli PING 2>/dev/null)" = PONG ] && return 0; sleep 2; done; return 1; }

cleanup(){
  case "$C" in
    redis)
      [ -n "${HOG_PID:-}" ] && kill "$HOG_PID" 2>/dev/null; rm -f "$HOG_FILE"
      wait_redis || log "redis: WARN not reachable for cleanup"
      docker exec redis redis-cli FLUSHALL >/dev/null 2>&1
      docker exec redis redis-cli SAVE     >/dev/null 2>&1   # shrink dump.rdb back
      ;;
    mysql)
      [ -n "${HOG_PID:-}" ] && kill "$HOG_PID" 2>/dev/null; rm -f "$HOG_FILE"
      # DROP the load table, then delete the binlogs: every inserted row is
      # also copied into the binlog (~90 MB per cycle otherwise). No replication
      # is configured, so RESET is safe. (PURGE ... BEFORE NOW() keeps the
      # just-rotated file and leaked ~900 MB per cycle.)
      wait_mysql || log "mysql: WARN not reachable for cleanup"
      docker exec mysql mysql -uroot -ppass -e \
        "DROP DATABASE IF EXISTS uc_load; RESET BINARY LOGS AND GTIDS;" >/dev/null 2>&1 \
        && log "mysql: load DB dropped, binlogs reset" || log "mysql: WARN cleanup failed"
      ;;
  esac
  log "complete: ${C} (free: $(free_gb) GB)"
}
trap cleanup EXIT

check_disk || exit 1
log "${C} for ${D}s (free: $(free_gb) GB)"

case "$C" in
  postgres)
    # One COPY per second → one vfs_write_error each; 3 in a 60 s window → 0.88.
    while [ "$(date +%s)" -lt "$END" ]; do
      docker exec postgres psql -U postgres -d postgres -q \
        -c "COPY (SELECT generate_series(1,400000)) TO '/dev/full';" >/dev/null 2>&1
      sleep 1
    done
    ;;

  redis)
    # noisy neighbour (same as mysql): bounded direct-I/O rewrite loop on the host
    ( while :; do dd if=/dev/zero of="$HOG_FILE" bs=4M count=$((HOG_MB/4)) \
          oflag=direct conv=fsync 2>/dev/null || break; done ) &
    HOG_PID=$!
    # Populate a bounded keyspace once, then repeatedly SAVE so redis-server
    # itself writes ~50 MB through the contended device.
    docker exec redis redis-benchmark -t set -n "$REDIS_KEYS" -r "$REDIS_KEYS" -d 4096 -q >/dev/null 2>&1
    while [ "$(date +%s)" -lt "$END" ]; do
      check_disk || break
      timeout 60 docker exec redis redis-cli SAVE >/dev/null 2>&1
      sleep 1
    done
    ;;

  mysql)
    # noisy neighbour: rewrite a bounded file with direct I/O + fsync in a loop
    ( while :; do dd if=/dev/zero of="$HOG_FILE" bs=4M count=$((HOG_MB/4)) \
          oflag=direct conv=fsync 2>/dev/null || break; done ) &
    HOG_PID=$!
    docker exec mysql mysql -uroot -ppass -e \
      "CREATE DATABASE IF NOT EXISTS uc_load;
       CREATE TABLE IF NOT EXISTS uc_load.t (id INT AUTO_INCREMENT PRIMARY KEY, p BLOB);" >/dev/null 2>&1
    rows=0
    while [ "$(date +%s)" -lt "$END" ] && [ "$rows" -lt "$MYSQL_MAX_ROWS" ]; do
      check_disk || break
      # 50 rows per statement, each an autocommitted 6 KB write from mysqld
      # (fsync per commit); the hog supplies the latency, not the volume.
      timeout 60 docker exec mysql mysql -uroot -ppass uc_load -e \
        "INSERT INTO t (p) SELECT REPEAT('x',6000) FROM information_schema.columns LIMIT 50;" >/dev/null 2>&1
      rows=$((rows + 50))
      sleep 0.5
    done
    # keep the hog (and mysqld's background flushing) going until END even if the row cap hit early
    while [ "$(date +%s)" -lt "$END" ]; do sleep 1; done
    ;;

  nginx)
    # nginx image has no ps/pkill; walk /proc with sh builtins and kill the
    # first worker (comm nginx, not PID 1). Master respawns it.
    while [ "$(date +%s)" -lt "$END" ]; do
      docker exec nginx sh -c '
        for p in /proc/[0-9]*; do
          pid=${p#/proc/}; [ "$pid" = 1 ] && continue
          [ "$(cat $p/comm 2>/dev/null)" = nginx ] && { kill "$pid"; exit 0; }
        done' >/dev/null 2>&1
      sleep 3
    done
    ;;

  *) log "no method for '$C'"; exit 1 ;;
esac
