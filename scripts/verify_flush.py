#!/usr/bin/env python3
"""
verify_flush.py — Gate for Step 1 of HANDOFF_v3: prove flush_io_queue() does
something observable inside each stateful workload.

For each container: read a durability counter, call flush_io_queue() exactly
as reconcile.py would, read the counter again, and assert it moved.

  postgres  pg_stat_checkpointer.num_requested      must increase by >= 1
  redis     INFO persistence rdb_last_save_time     must advance (>= +1 s)
  mysql     Innodb_data_fsyncs                       must increase (redo fsync)
            and the binlog file (SHOW BINARY LOG STATUS) must rotate.
            NOTE: Innodb_buffer_pool_pages_dirty is NOT a valid check — no SQL
            statement synchronously writes the buffer pool; InnoDB durability
            is the fsync'd redo log, which is what FLUSH LOGS forces.

Exit status 0 only if every container passes.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "stateful"))
sys.path.insert(0, ROOT)
from actions import flush_io_queue  # noqa: E402


def dx(container, *cmd):
    p = subprocess.run(["docker", "exec", container, *cmd],
                       capture_output=True, text=True, timeout=30)
    return p.stdout.strip()


def pg_requested():
    return int(dx("postgres", "psql", "-U", "postgres", "-tAc",
                  "SELECT num_requested FROM pg_stat_checkpointer;"))


def redis_last_save():
    out = dx("redis", "redis-cli", "INFO", "persistence")
    return int(next(l for l in out.splitlines()
                    if l.startswith("rdb_last_save_time:")).split(":")[1])


def mysql_fsyncs():
    out = dx("mysql", "mysql", "-uroot", "-ppass", "-N", "-e",
             "SHOW GLOBAL STATUS LIKE 'Innodb_data_fsyncs';")
    return int(out.split()[-1])


def mysql_binlog():
    out = dx("mysql", "mysql", "-uroot", "-ppass", "-N", "-e",
             "SHOW BINARY LOG STATUS;")
    return out.split()[0] if out else ""


def mysql_write_some_rows():
    # Give the flush something to do: write rows so there is pending state.
    dx("mysql", "mysql", "-uroot", "-ppass", "-e",
       "CREATE DATABASE IF NOT EXISTS uc_verify; "
       "CREATE TABLE IF NOT EXISTS uc_verify.t (id INT AUTO_INCREMENT PRIMARY KEY, v VARBINARY(1024)); "
       "INSERT INTO uc_verify.t (v) SELECT RANDOM_BYTES(1024) FROM "
       "(SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) a, "
       "(SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) b, "
       "(SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) c; ")


results = {}

# ── postgres ──────────────────────────────────────────────────────────────────
before = pg_requested()
r = flush_io_queue("postgres")
after = pg_requested()
results["postgres"] = {
    "counter": "pg_stat_checkpointer.num_requested",
    "before": before, "after": after, "success": r["success"],
    "flush_latency_ms": r["flush_latency_ms"],
    "pass": r["success"] and after >= before + 1,
}

# ── redis ─────────────────────────────────────────────────────────────────────
before = redis_last_save()
time.sleep(1.1)   # rdb_last_save_time has 1 s resolution
r = flush_io_queue("redis")
after = redis_last_save()
results["redis"] = {
    "counter": "rdb_last_save_time",
    "before": before, "after": after, "success": r["success"],
    "flush_latency_ms": r["flush_latency_ms"],
    "pass": r["success"] and after > before,
}

# ── mysql ─────────────────────────────────────────────────────────────────────
mysql_write_some_rows()
before_fs = mysql_fsyncs()
before_log = mysql_binlog()
r = flush_io_queue("mysql")
after_fs = mysql_fsyncs()
after_log = mysql_binlog()
results["mysql"] = {
    "counter": "Innodb_data_fsyncs / binlog file",
    "before": {"fsyncs": before_fs, "binlog": before_log},
    "after":  {"fsyncs": after_fs,  "binlog": after_log},
    "success": r["success"],
    "flush_latency_ms": r["flush_latency_ms"],
    "pass": r["success"] and after_log != before_log and after_fs > before_fs,
}

# ── unknown container must be refused, not faked ──────────────────────────────
r = flush_io_queue("nginx")
results["nginx"] = {"counter": "n/a (no durable state)", "success": r["success"],
                    "pass": r["success"] is False}

results["_meta"] = {"timestamp": time.time(),
                    "date": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
out = os.path.join(ROOT, "evaluation", "results", "flush_verification.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as fh:
    json.dump(results, fh, indent=2)
print(json.dumps(results, indent=2))
print("written:", out)
ok = all(v["pass"] for k, v in results.items() if not k.startswith("_"))
print("\nGATE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
