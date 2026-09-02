#!/usr/bin/env python3
"""
run_experiment.py — Podman checkpoint/restore feasibility test.

EXPERIMENTAL. Fully isolated from the paper's evaluation:
  - Uses throwaway containers (pg-podman-ckpt-exp, redis-podman-ckpt-exp,
    mysql-podman-ckpt-exp), never the docker-compose.yml postgres/redis/mysql
    containers or their pgdata/mysqldata volumes.
  - Writes results only to podman_experiment/results.json — never touches
    data/phase2/, results/, or scripts/episodes_*.jsonl.
  - Does not modify docker-compose.yml or run `docker compose up/down`.

Procedure (per the task spec):
  1. Start a fresh throwaway container for the workload.
  2. Test checkpoint CREATION independently. If it fails, record the exact
     error and STOP for that workload (no restore attempt).
  3. If creation succeeds, test RESTORE. Record exact error if it fails.
  4. If both succeed, timing (creation_ms, restore_ms) was already captured
     via time.perf_counter() around each subprocess call — the same
     methodology stateful/actions.py uses for ckpt_latency_ms, and
     consistent in spirit with scripts/run_episodes.py's wall-clock timing.
  5. Clean up the throwaway container/checkpoint archive.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from pinned_digests import CONTAINERS
from podman_actions import podman_checkpoint, podman_restore

_HERE = os.path.dirname(__file__)
RESULTS_PATH = os.path.join(_HERE, "results.json")
CKPT_DIR = os.path.join(_HERE, "ckpt_archives")


def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"


def start_container(workload: str, spec: dict) -> dict:
    """Start a fresh throwaway container. Removes any stale one first."""
    name = spec["name"]
    _run(["podman", "rm", "-f", name], timeout=30)  # clean slate, ignore errors

    # crun 0.17 on this host lacks +CRIU (not built with libcriu). runc 1.3.4
    # (pulled in by the Docker install) shells out to the standalone `criu`
    # binary instead, so it doesn't need to be compiled with CRIU support.
    cmd = ["podman", "run", "-d", "--runtime", "runc", "--name", name]
    for e in spec["env"]:
        cmd += ["-e", e]
    cmd.append(spec["image"])

    rc, out, err = _run(cmd, timeout=60)
    return {"started": rc == 0, "stdout": out, "stderr": err}


def wait_ready(workload: str, name: str, max_wait: float = 30) -> bool:
    """Poll `podman inspect` until the container reports Running."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        rc, out, _ = _run(
            ["podman", "inspect", "--format", "{{.State.Running}}", name], timeout=10
        )
        if rc == 0 and out.strip() == "true":
            time.sleep(3)  # let the DB process finish booting inside the container
            return True
        time.sleep(1)
    return False


def cleanup(name: str, ckpt_path: str | None):
    _run(["podman", "rm", "-f", name], timeout=30)
    if ckpt_path and os.path.exists(ckpt_path):
        os.remove(ckpt_path)


def test_workload(workload: str, spec: dict) -> dict:
    name = spec["name"]
    print(f"\n{'='*60}\n[podman-exp] {workload}: starting {name}")

    result = {"workload": workload, "container": name}

    started = start_container(workload, spec)
    if not started["started"]:
        result["stage"] = "start"
        result["error"] = started["stderr"]
        print(f"[podman-exp] {workload}: FAILED to start — {started['stderr']}")
        return result

    if not wait_ready(workload, name):
        result["stage"] = "start"
        result["error"] = "container did not reach Running state in time"
        print(f"[podman-exp] {workload}: FAILED — did not become ready")
        cleanup(name, None)
        return result

    print(f"[podman-exp] {workload}: running, testing checkpoint creation")

    # In-place mode (no --export/--import): checkpoint and restore act on
    # the SAME container object, which is the direct analog of Docker's
    # `checkpoint create` + `start --checkpoint` on one container — and
    # avoids two --export/--import-only quirks found during this
    # experiment: (1) anonymous volumes re-created from the archive collide
    # with the still-existing original volume, (2) --export does NOT
    # remove the original (now-stopped) container, so --import under the
    # same name collides with it.
    ckpt = podman_checkpoint(name)
    result["checkpoint"] = ckpt

    if not ckpt["success"]:
        result["stage"] = "checkpoint"
        print(f"[podman-exp] {workload}: CHECKPOINT FAILED — {ckpt['stderr']}")
        cleanup(name, None)
        return result

    print(f"[podman-exp] {workload}: checkpoint OK ({ckpt['latency_ms']}ms), testing restore")

    restore = podman_restore(name)
    result["restore"] = restore
    result["stage"] = "restore" if not restore["success"] else "done"

    if restore["success"]:
        print(f"[podman-exp] {workload}: RESTORE OK ({restore['latency_ms']}ms)")
    else:
        print(f"[podman-exp] {workload}: RESTORE FAILED — {restore['stderr']}")

    cleanup(name, None)
    return result


def main():
    # Optional CLI args restrict which workloads run (e.g. `postgres redis`
    # to re-test only those two after a CRIU rebuild, skipping mysql since
    # its podman-path failure is a missing --file-locks CLI flag, unrelated
    # to CRIU version). No args = all three, same as before.
    workloads = sys.argv[1:] or list(CONTAINERS.keys())
    out_path = os.environ.get("RESULTS_PATH_OVERRIDE", RESULTS_PATH)

    results = {}
    for workload in workloads:
        results[workload] = test_workload(workload, CONTAINERS[workload])

    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}\n[podman-exp] results written to {out_path}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
