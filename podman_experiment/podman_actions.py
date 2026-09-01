# podman_actions.py — EXPERIMENTAL Podman checkpoint/restore path
#
# This module is NOT wired into stateful/actions.py or the production
# executor's dispatch table. It exists purely to test a hypothesis:
#
#   Docker's checkpoint/restore is broken in two independently-diagnosed ways
#   (see stateful/actions.py docstring and docker-compose.yml CRIU notes):
#     - postgres/redis restore: Docker resolves the restored task to PID 0
#       and fails bind-mounting /proc/0/ns/net (containerd#12141, closed
#       not-planned upstream).
#     - mysql checkpoint creation: mysqld holds POSIX file locks that CRIU
#       needs --file-locks to handle; Docker's CLI does not expose that flag.
#
#   Podman implements checkpoint/restore via a separate code path
#   (`podman container checkpoint` / `podman container restore`), maintained
#   independently of Docker/containerd/runc's checkpoint plumbing. It may
#   not carry either defect.
#
# The existing Docker path in stateful/actions.py::checkpoint_and_restart is
# left completely untouched by this module — same behavior, same dispatch,
# same fallback-to-restart semantics. This file only adds a parallel,
# opt-in path for manual experimentation against throwaway containers.

import subprocess
import time


def _criu_log_tail(container: str, log_name: str, n: int = 40) -> str:
    """
    Best-effort fetch of the actual CRIU log for `container`.

    podman/runc only surface a generic wrapper error (e.g. "criu failed:
    type RESTORE errno 0") on the CLI; the real reason is written to
    dump.log / restore.log under the container's userdata directory. This
    is diagnostic-only — failures here never affect the pass/fail verdict.
    """
    try:
        r = subprocess.run(
            ["podman", "inspect", "--format", "{{.Id}}", container],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return f"(could not resolve container id: {r.stderr.strip()})"
        cid = r.stdout.strip()

        r = subprocess.run(
            ["podman", "info", "--format", "{{.Store.GraphRoot}}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return f"(could not resolve graph root: {r.stderr.strip()})"
        graph_root = r.stdout.strip()

        log_path = f"{graph_root}/overlay-containers/{cid}/userdata/{log_name}"
        with open(log_path, "r", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]) if lines else "(log empty)"
    except Exception as e:  # noqa: BLE001 — diagnostic path, never fatal
        return f"(could not read {log_name}: {e})"


def podman_checkpoint(container: str, export_path: str | None = None,
                       timeout: float = 60) -> dict:
    """
    Run `podman container checkpoint [--export <path>] <container>`.

    Mirrors the timing methodology used by stateful/actions.py's
    checkpoint_and_restart(): wall time is measured with time.perf_counter()
    bracketing the subprocess call, reported in milliseconds.
    """
    cmd = ["podman", "container", "checkpoint"]
    if export_path:
        cmd += ["--export", export_path]
    cmd.append(container)

    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ok = r.returncode == 0
        stdout, stderr = r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        ok = False
        stdout, stderr = "", "podman not found"
    except subprocess.TimeoutExpired:
        ok = False
        stdout, stderr = "", f"podman container checkpoint timed out after {timeout}s"
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    result = {
        "container":   container,
        "phase":       "checkpoint",
        "success":     ok,
        "latency_ms":  latency_ms,
        "stdout":      stdout,
        "stderr":      stderr,
        "export_path": export_path,
        "cmd":         cmd,
    }
    if not ok:
        result["criu_log_tail"] = _criu_log_tail(container, "dump.log")
    return result


def podman_restore(container: str, import_path: str | None = None,
                    timeout: float = 60) -> dict:
    """
    Run `podman container restore [--import <path>] <container>`.

    If import_path is None, this restores in place from the checkpoint
    state podman retained internally after podman_checkpoint() (only valid
    if the checkpointed container was not removed).

    `podman container restore` does not accept a positional CONTAINER
    argument together with --import (the archive fully describes the
    container to recreate) — use --name instead to assign the recreated
    container the same name as the original.
    """
    cmd = ["podman", "container", "restore"]
    if import_path:
        cmd += ["--import", import_path, "--name", container]
    else:
        cmd.append(container)

    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ok = r.returncode == 0
        stdout, stderr = r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        ok = False
        stdout, stderr = "", "podman not found"
    except subprocess.TimeoutExpired:
        ok = False
        stdout, stderr = "", f"podman container restore timed out after {timeout}s"
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    result = {
        "container":   container,
        "phase":       "restore",
        "success":     ok,
        "latency_ms":  latency_ms,
        "stdout":      stdout,
        "stderr":      stderr,
        "import_path": import_path,
        "cmd":         cmd,
    }
    if not ok:
        result["criu_log_tail"] = _criu_log_tail(container, "restore.log")
    return result
