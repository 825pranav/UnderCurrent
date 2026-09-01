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
#   * the block-I/O throttle (cgroup v2 io.max) is always reset on exit
#
# METHOD PER CONTAINER
#   postgres  backend COPY ... TO '/dev/full'  → ENOSPC from comm "postgres"
#             signal: vfs_write_error  (3 in window → 0.88 ≥ τ_repair)
#   redis     redis-server SAVE under a write throttle → slow bios from
#             comm "redis-server"; signal: blk_io_latency ≥ 10 ms
#             (5 in window → 0.85 ≥ τ_repair)
#   mysql     mysqld synchronous commits (flush_log_at_trx_commit=1, O_DIRECT)
#             under the same throttle; signal: blk_io_latency from comm "mysqld"
#   nginx     Type-S: kill one worker so sched_process_exit sees comm "nginx";
#             the master (PID 1) respawns it.
#
# Usage: ./inject_fault_v2.sh <container> [duration_s]
# Needs: docker group for docker exec; sudo (cached via `sudo -v`) only for
#        writing io.max on redis/mysql.

set -uo pipefail
C="${1:?Usage: inject_fault_v2.sh <container> [duration_s]}"
D="${2:-30}"
END=$(( $(date +%s) + D ))

MIN_FREE_GB="${MIN_FREE_GB:-8}"
THROTTLE_BPS="${THROTTLE_BPS:-1048576}"     # 1 MiB/s write cap during injection
BLKDEV="${BLKDEV:-253:0}"                   # ubuntu--vg-ubuntu--lv (lsblk MAJ:MIN)
MYSQL_MAX_ROWS="${MYSQL_MAX_ROWS:-1500}"    # 1500 × 60 KB ≈ 90 MB ceiling
REDIS_KEYS="${REDIS_KEYS:-20000}"           # 20000 × 4 KB ≈ 80 MB RDB ceiling

log(){ echo "[inject-v2] $(date +%H:%M:%S) $*"; }

free_gb(){ df -BG --output=avail / | tail -1 | tr -dc '0-9'; }
check_disk(){
  local f; f=$(free_gb)
  if [ "$f" -lt "$MIN_FREE_GB" ]; then
    log "ABORT: only ${f} GB free (< ${MIN_FREE_GB} GB)"; return 1
  fi
}

cg_path(){ echo "/sys/fs/cgroup/system.slice/docker-$(docker inspect "$1" --format '{{.Id}}').scope"; }
throttle_on(){
  local p; p=$(cg_path "$1")
  echo "$BLKDEV wbps=$THROTTLE_BPS" | sudo -n tee "$p/io.max" >/dev/null \
    && log "$1: io.max wbps=$THROTTLE_BPS" || log "$1: WARN could not set io.max (sudo not cached?)"
}
throttle_off(){
  local p; p=$(cg_path "$1")
  echo "$BLKDEV wbps=max" | sudo -n tee "$p/io.max" >/dev/null 2>&1 && log "$1: io.max reset"
}

cleanup(){
  case "$C" in
    redis)
      throttle_off redis
      docker exec redis redis-cli FLUSHALL >/dev/null 2>&1
      docker exec redis redis-cli SAVE     >/dev/null 2>&1   # shrink dump.rdb back
      ;;
    mysql)
      throttle_off mysql
      # DROP the load table, then rotate + purge binlogs: every inserted row is
      # also copied into the binlog, which would otherwise grow ~90 MB per cycle.
      docker exec mysql mysql -uroot -ppass -e \
        "DROP DATABASE IF EXISTS uc_load; FLUSH LOGS; PURGE BINARY LOGS BEFORE NOW();" >/dev/null 2>&1
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
    throttle_on redis
    # Populate a bounded keyspace once, then repeatedly SAVE so redis-server
    # itself writes ~80 MB through the throttled device.
    docker exec redis redis-benchmark -t set -n "$REDIS_KEYS" -r "$REDIS_KEYS" -d 4096 -q >/dev/null 2>&1
    while [ "$(date +%s)" -lt "$END" ]; do
      check_disk || break
      timeout 60 docker exec redis redis-cli SAVE >/dev/null 2>&1
      sleep 1
    done
    ;;

  mysql)
    throttle_on mysql
    docker exec mysql mysql -uroot -ppass -e \
      "CREATE DATABASE IF NOT EXISTS uc_load;
       CREATE TABLE IF NOT EXISTS uc_load.t (id INT AUTO_INCREMENT PRIMARY KEY, p BLOB);" >/dev/null 2>&1
    rows=0
    while [ "$(date +%s)" -lt "$END" ] && [ "$rows" -lt "$MYSQL_MAX_ROWS" ]; do
      check_disk || break
      # 50 rows per statement, each an autocommitted 60 KB write from mysqld.
      timeout 60 docker exec mysql mysql -uroot -ppass uc_load -e \
        "INSERT INTO t (p) SELECT REPEAT('x',60000) FROM information_schema.columns LIMIT 50;" >/dev/null 2>&1
      rows=$((rows + 50))
      # recycle: once at the cap, truncate and keep going until END
      if [ "$rows" -ge "$MYSQL_MAX_ROWS" ] && [ "$(date +%s)" -lt "$END" ]; then
        docker exec mysql mysql -uroot -ppass -e "TRUNCATE uc_load.t;" >/dev/null 2>&1
        rows=0
      fi
    done
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
