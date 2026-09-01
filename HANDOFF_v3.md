# UnderCurrent — Handoff v3 (2026-09-01)

Read this first. It supersedes `paper_issues_and_fixes.md` and the v2 handoff.
Everything below was verified on the VM, not inferred.

Branch: `fixes` (pushed to origin). Commits `c9e5c17`, `6f6e414`.
Untracked: `archive/`, `scripts/inject_fault_v2.sh`.

---

## 1. What is actually hollow in the paper

Be honest about this before deciding anything. Five things:

**1. The flush does nothing.** `stateful/actions.py::flush_io_queue()` prints a
string and returns. Its own docstring says "Simulated here." The Intro claims
skipping flush "risk[s] corrupting transactions that haven't committed yet."
Nothing in the system flushes anything. Only the *ordering* was ever verified.

**2. The corpus is one container.** All 9,132 traces in `data/phase2/` are
`postgres`, on both tracks. §VII-A and the Abstract both claim four. Diagnosed
fully: faults were injected into postgres only; nginx was provably live but
idle; redis presumed live; **mysql did not exist yet** (created 2026-06-19, two
months after the 2026-04-11/12 corpus window).

**3. WASM was never active in the corpus.** Footnote (b) says the corpus
"predates wasmtime activation." That is probably wrong — see trap A below. The
real reason is almost certainly that the run was under `sudo`, and wasmtime is a
`--user` install root cannot see. The same silent fallback happened again on
2026-09-01 until it was fixed.

**4. M2 = M6 = 0.0193 is structural, not empirical.** 44 divergences from a
single container's fault sequence. The paper explains why they're equal, which
is honest, but it is a thin result.

**5. CRIU never succeeded.** Correctly documented now (Docker PID-0 bug;
mysqld's 17 POSIX file locks with runc not passing `--file-locks`; upstream
containerd#12141 closed as not planned). Not fixable, correctly a Limitation.

**Realistic expectation:** a rerun makes the numbers *more defensible*, not
necessarily larger or prettier. Multi-container coverage and a real flush remove
two genuine soft spots. Do not expect dramatically different M1–M6 values.

---

## 2. Traps — every one of these was hit for real

**A. `sudo` + user-installed wasmtime → silent WASM fallback.**
wasmtime lives in `/home/pes2ug23cs429/.local/lib/python3.10/site-packages`.
Under `sudo`, root ignores `~/.local`, `import wasmtime` fails, and
`actions.py` catches the ImportError and falls back to the Python gate with only
a `RuntimeWarning`. Launch must be:
```bash
sudo env PYTHONPATH=/home/pes2ug23cs429/.local/lib/python3.10/site-packages \
  python3 run.py --real --no-dashboard
```
**Gate:** `grep -ci "WASM-FALLBACK" /tmp/uc_rerun.log` must return `0`.

**B. `scripts/inject_fault.sh` cannot work.** It runs `dd` inside the container.
The eBPF listeners attribute by `comm` against
`{nginx, postgres, redis-server, mysqld}`. `dd`'s comm is `dd`, so every event
is dropped. Proven 2026-09-01: injecting into all three produced 26 postgres
records (its own idle activity, max score 0.293) and **zero** for redis/mysql.
Fault injection must make the **server process itself** do the I/O.

**C. `scripts/inject_fault_v2.sh` filled the disk.** Its mysql branch does
unbounded 60 KB BLOB inserts; 20 seconds wrote ~720 MB and took the filesystem
to 0 bytes. **Do not run it as written.** It needs a row cap and a `df` check
per iteration.

**D. The trace logger appends** (`open(TRACE_FILE, "a")`). A rerun blends into
the previous corpus invisibly. Archive `stateful/traces.jsonl`,
`stateless/traces.jsonl`, `stateful/divergence_log.jsonl` before every run.
April's are already at `archive/april-corpus-20260901-182347/`.

**E. Backgrounded `sudo` gets SIGTTIN.** `nohup sudo ... &` stops immediately
because sudo cannot prompt. Run `sudo -v` in the foreground first.

**F. Never `docker compose up/down`.** Use `docker restart <name>`. Compose is
now digest-pinned with the two anonymous volumes declared `external`, but a
full cycle is still unnecessary risk.

**G. Disk.** The filesystem was 30 G inside a 60.95 G logical volume until
`resize2fs` on 2026-09-01. Now 60 G, ~34 G free. ROS/Gazebo purged (429
packages, 0 remain).

---

## 3. Sequence

Each step has a gate. Do not proceed past a failed gate.

### Step 0 — Decide scope (5 min, human decision)

Two viable paths. Pick one before touching anything:

- **Path R (reword only, ~30 min).** Split §VII-A and the Abstract into two
  accurately-scoped sentences. No new data. Every published metric stands. The
  paper is already Accept/High confidence and the reviewers asked for neither
  multi-container coverage nor WASM.
- **Path F (make it true, ~1–2 days).** Steps 1–6 below. Genuinely fixes the
  flush, the coverage, and WASM. Changes every M1–M6 value everywhere.

Path R wording is in handoff v2 and remains accurate. What follows is Path F.

### Step 1 — Make the flush real (~2 h)

This is the highest-value change: it converts the Intro's claim from unsupported
to true, and it does not need the 20-hour run.

In `stateful/actions.py`, replace the no-op with the workload's own primitive:
```python
_FLUSH_CMD = {
    "postgres": ["psql", "-U", "postgres", "-c", "CHECKPOINT;"],
    "redis":    ["redis-cli", "SAVE"],
    "mysql":    ["mysql", "-uroot", "-ppass", "-e", "FLUSH TABLES; FLUSH LOGS;"],
}
```
Run via `docker exec`, time it, return `flush_latency_ms`.

**Gate — prove it did something:**
- postgres: `SELECT checkpoints_req FROM pg_stat_bgwriter;` increments
- redis: `INFO persistence` → `rdb_last_save_time` advances
- mysql: `SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_dirty'` drops

Note `redis-cli SAVE` is synchronous and blocking — correct for a
"flush completed before checkpoint" claim. Do not use BGSAVE.

This upgrades the guarantee from "control-plane ordering" to "a verified durable
flush completes before restart is authorised," which is stronger than anything a
working CRIU restore would have given.

### Step 2 — Fix the injector (~1–2 h)

Rewrite `scripts/inject_fault_v2.sh` so all I/O originates in the server process
**and** is bounded.

- **postgres** — works already, verified:
  `COPY (SELECT generate_series(1,400000)) TO '/dev/full';`
  returns `ERROR: could not write to COPY file: No space left on device`.
  comm = `postgres`, produces `vfs_write_error`. This is the April method.
- **redis** — `CONFIG SET dir` is a protected config in Redis 8, so `/dev/full`
  is unreachable. Use `redis-benchmark -t set` plus synchronous `SAVE` so
  `redis-server` writes the RDB itself. Target `blk_io_latency` ≥ 10 ms.
- **mysql** — `secure_file_priv=/var/lib/mysql-files/` blocks `OUTFILE` to
  `/dev/full`. Use bounded inserts: `innodb_flush_log_at_trx_commit=1` with
  `O_DIRECT` means every commit is a synchronous write from `mysqld`.
  **Cap total rows and check `df` each iteration.** Trap C.
- **nginx** — Type-S: `pkill -o -x nginx` gives `process_exit` with comm `nginx`.

**Gate:** run each for 60 s with the controller up and confirm records appear
for that container with score ≥ 0.50 (τ_flush). Scoring reference: `blk_io`
θ=5 → 0.85; `vfs` θ=3 → 0.88; τ_flush 0.50, τ_repair 0.80.
**Do not start the long run until all four pass.**

### Step 3 — Launch the corpus run (~20 h)

```bash
# archive previous traces (trap D)
mkdir -p archive/run-$(date +%Y%m%d-%H%M%S)
mv stateful/traces.jsonl stateless/traces.jsonl stateful/divergence_log.jsonl \
   archive/run-*/ 2>/dev/null

for c in postgres redis mysql nginx; do docker restart $c; done   # trap F
sleep 15
sudo -v                                                            # trap E
nohup sudo env PYTHONPATH=/home/pes2ug23cs429/.local/lib/python3.10/site-packages \
  python3 run.py --real --no-dashboard > /tmp/uc_rerun.log 2>&1 &
sleep 30
grep -ci "WASM-FALLBACK" /tmp/uc_rerun.log     # MUST be 0 — trap A
```
Keep `--interval` at 5 s; it is a reported experimental parameter.
Inject into all four on a rotation across the window, not once at the start.

**Gate at 10 minutes:** confirm records exist for all four containers with
scores crossing τ_flush. If only postgres appears, stop — step 2 did not work.

### Step 4 — Naive baseline (~25 min, run in parallel)

Parameterize `scripts/run_episodes.py` (~15–20 lines) to accept a controller
that dispatches `checkpoint_and_restart` immediately on signal.
**Trap:** do not route it through `execute()` — that applies the same policy
gate and silently yields fake 30/30 timeouts (a placeholder `BaselineController`
with `score=-1.0` already shows this). Call the action function directly.
Frame it in the paper as a naive local controller, not a Kubernetes policy.

### Step 5 — Recompute and update the paper

New corpus hash will differ from `d543e125…` — that is intended.
Update every M1–M6 citation in the Abstract, §VII, Table II, and the ablation.
Remove footnote (b) if WASM was genuinely active. Rewrite §VII-A with the real
container coverage. Revisit the Intro now that the flush is real.

### Step 6 — Compile

No TeX toolchain on the VM. Everything so far is structural checks only
(balanced environments/braces/math, resolved cites and refs). Get a real
`pdflatex` run before submitting.

---

## 4. Still open regardless of path

- **Intro flush wording** — resolved by step 1; otherwise needs softening.
- **§VII-A + Abstract four-container claim** — both conflate the trace corpus
  with the MTTR campaign.
- **Compose provenance** — the original containers were created by hand and the
  command was never recorded. Digests now pinned from the running containers.
- **`stateless/main.py:51`** — stale list (`pyapp`, `worker`, `gateway` are not
  real). Harmless, asymmetric with the curated Type-F list.
- **`shared/metrics.py:396–444`** — leftover `etcd` test fixtures.
- **GitHub PAT in `.git/config`** — plaintext in the remote URL. Rotate it and
  switch to a credential helper.

## 5. Do not attempt

- **CRIU restore.** Both failures are Docker/runc integration defects, not
  kernel restrictions. Upstream containerd#12141 is closed as not planned and
  reproduces on kernel 6.8. A kernel upgrade fixes nothing and risks the VM.
- **Podman migration.** ~half a day; would strengthen a limitation that is
  already well handled. Stretch goal only.
- **`docker system prune -a`.** Removes images the pinned compose file needs.
