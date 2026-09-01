# Podman checkpoint/restore feasibility experiment — results

Branch: `podman-checkpoint-experiment` (branched from `fixes`, never merged back).
Fully isolated from the paper's evaluation: throwaway containers only
(`pg-podman-ckpt-exp`, `redis-podman-ckpt-exp`, `mysql-podman-ckpt-exp`),
no interaction with `data/phase2/`, `results/`, `docker-compose.yml`, or the
`pgdata`/`mysqldata` volumes. Raw per-run output (including full CRIU log
tails) is in `results.json`, written by `run_experiment.py`.

## Environment

- Podman 3.4.4, CRIU 3.16.1 (both from Ubuntu 22.04 `universe`, installed via apt)
- Kernel 6.8.0-136-generic
- Images: exact digests pinned in `docker-compose.yml` (postgres, redis, mysql) — verified identical digest after `podman pull`, no version drift
- **Rootless podman refuses checkpoint/restore outright** (`Error: checkpointing a container requires root`) — had to switch to rootful (`sudo podman`) to test at all
- **Rootful default runtime (`crun` 0.17) also refuses it** — `crun features` shows no `+CRIU` flag; this apt-packaged `crun` was not compiled against libcriu
- Working combination: rootful podman + `--runtime runc` (runc 1.3.4, installed alongside Docker, shells out to the standalone `criu` binary rather than needing to be compiled with CRIU support)

## Results

| Workload | Checkpoint creation | Restore | Root cause |
|---|---|---|---|
| postgres | **PASS** (~0.5–1.1s) | **FAIL** | CRIU restore error: `Can't open file dev/shm/PostgreSQL.<n> on restore: No such file or directory` — a POSIX shared-memory segment postgres created isn't reconstructable on restore |
| redis | **PASS** (~0.4–0.6s) | **FAIL** | Restored process **segfaults immediately**: `Error (criu/cr-restore.c:1492): <pid> stopped by signal 11: Segmentation fault` |
| mysql | **FAIL** | not attempted (per spec: no restore test after creation failure) | `Error (criu/file-lock.c:110): Some file locks are hold by dumping tasks! You can try --file-locks to dump them.` — **identical root cause to the Docker failure** |

Exact commands, full stdout/stderr, and CRIU log tails for every run are in `results.json`.

## Interpretation

**mysql: not fixed by switching to Podman.** This is the same CRIU limitation
that blocks Docker — mysqld's POSIX file locks require CRIU's `--file-locks`
flag, and neither Docker's CLI nor Podman's CLI (nor the `runc checkpoint`/
`restore` commands they both ultimately invoke) exposes that flag. Checked
`podman container checkpoint --help`: no `--file-locks` option exists. This
is not a Docker-specific gap; it's absent from Podman's CLI surface too.

**postgres and redis: Podman's checkpoint/restore path is confirmed to be
genuinely independent of Docker's implementation** — neither hit Docker's
`containerd#12141` PID-0/`/proc/0/ns/net` bind-mount bug. But independence
did not translate into success: both failed at the CRIU level for reasons
that look like a **CRIU-version/kernel-version mismatch** — CRIU 3.16.1
(released ~2021) running against kernel 6.8.0 (a 2024+ kernel). The redis
segfault-on-restore in particular is a known class of issue when an old CRIU
build doesn't correctly reconstruct process state for a kernel much newer
than it was validated against. A newer CRIU (3.19+) built against this
kernel might resolve the postgres/redis failures — that's outside the scope
of what an `apt install podman criu` feasibility check can test, and even if
it did, it would not fix mysql, since that requires Podman's CLI (not just
CRIU) to grow a `--file-locks` passthrough it doesn't currently have.

## Timing vs. existing MTTR numbers

No workload achieved full checkpoint+restore success, so there is no
like-for-like MTTR figure to report, and none of the captured latencies
should be read as a comparison to the existing docker-restart MTTR numbers
(postgres 10.87s, redis 10.40s, mysql 11.54s) — those measure a full
fault-detection-to-recovery loop, not a single subprocess call. For
reference only: checkpoint-creation-alone latency was ~0.5–1.1s (postgres)
and ~0.4–0.6s (redis), consistently well under a second — but this is not
a "restore succeeded X times faster" claim, since restore never succeeded.

## Bottom line

On this machine's current toolchain (Ubuntu 22.04 apt-packaged Podman
3.4.4 / CRIU 3.16.1), migrating to Podman does **not** unblock any of the
three workloads:

- mysql fails identically to Docker, for the same underlying reason.
- postgres and redis fail differently than Docker (confirming the two
  implementations are independent), but still fail, at the CRIU layer,
  likely due to CRIU/kernel version skew rather than a Docker-specific defect.

This is a negative feasibility result for the toolchain tested. It does not
rule out Podman + a newer CRIU build resolving postgres/redis — that would
need a separate, more invasive experiment (building CRIU from source against
this kernel) that was out of scope here.
