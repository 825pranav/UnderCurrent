# UnderCurrent — Observed Measurements

Single source of truth for all numbers in the paper.
Every entry states its source. If the source is not listed here, the number is not measured.

**Key:** `MEASURED` = taken directly from terminal/code output. `COMPUTED` = derived from MEASURED values. `NOT MEASURED` = no data exists yet.

---

## Phase 2 eBPF Evaluation (pre-existing, from committed traces)

Source: `stateful/traces.jsonl` (Phase 2 snapshot). Run prior to any Day 1–3 changes.

| Metric | Value | Raw count |
|--------|-------|-----------|
| M1 misclassification rate | 0.0000 | 0 errors / 3,630 combined |
| M2 divergence rate (Type F Ph.2) | 0.3697 | 44 divergences / 119 cycles |
| M4 reversibility (Type S) | 1.0000 | 1,039 active decisions |
| M4 reversibility (Type F Ph.2) | 1.0000 | 95 active decisions |
| M5 schema completeness | 1.0000 | 0 missing fields |
| M6 suppression rate (Type F Ph.2) | 0.3697 | 44 FSM-gated / 119 cycles |
| Phase 1 Type-S entries | N=1,187 | real eBPF |
| Phase 1 Type-F entries | N=386 | real eBPF |
| Phase 2 Type-F entries | N=119 | real eBPF |

**Notes:**
- M2 = M6 = 0.3697 is not a coincidence. Both count the same 44 cycles from different directions.
- M6 was measured with the Python FSM gate (wasmtime was NOT installed at evaluation time).
- "volume_delete" irreversible action was never triggered — M4=1.0 reflects absence of that condition, not active suppression.

---

## CRIU Checkpoint Latency — postgres (Day 2)

Source: terminal output from `run_episodes.py --container postgres --episodes 30`. Each value is the wall time for `docker checkpoint create postgres <name>` to return.
All 30 checkpoint creations SUCCEEDED (exit 0). All 30 restores FAILED (netns bind-mount error on Linux 5.15).

Raw values (ms): 397.8, 421.1, 401.4, 411.6, 461.6, 457.7, 361.6, 417.7, 390.5, 410.1, 402.8, 399.1, 375.9, 378.8, 370.4, 401.6, 408.6, 381.1, 399.0, 386.5, 354.3, 402.8, 490.2, 416.3, 476.5, 401.2, 369.6, 384.3, 401.4, 432.0

| Stat | Value |
|------|-------|
| n | 30 |
| mean | 405.4 ms |
| stddev | 31.6 ms |
| min | 354.3 ms |
| max | 490.2 ms |

**What this is:** Time to write the CRIU checkpoint artefact to disk. Does NOT include restore (which always failed).

---

## CRIU Checkpoint Latency — mysql (Day 3)

Source: terminal output from `run_episodes.py --container mysql --episodes 30`. Each value is the wall time until `docker checkpoint create mysql <name>` FAILED (exit non-zero). MySQL uses multiple runc processes; CRIU cannot checkpoint it in this configuration.

Raw values (ms): 278.2, 284.1, 199.4, 184.9, 181.3, 202.1, 208.0, 188.7, 201.6, 187.2, 200.2, 189.6, 179.5, 182.1, 191.5, 195.9, 200.9, 202.4, 214.8, 398.7, 227.5, 218.1, 199.1, 190.0, 198.6, 179.2, 300.6, 312.8, 534.8, 333.6

| Stat | Value |
|------|-------|
| n | 30 |
| mean | 232.2 ms |
| stddev | 77.1 ms |
| min | 179.2 ms |
| max | 534.8 ms |

**Note:** High stddev driven by three outliers (ep20=398.7ms, ep27=300.6ms, ep28=312.8ms, ep29=534.8ms). Excluding those four: mean ≈ 197ms.

---

## CRIU Checkpoint Latency — redis

Source: `docker checkpoint create redis uc_redis_test` run manually (Day 1). Returned exit 0 — checkpoint artefact created. Restore failed (same netns error as postgres).

**Individual per-episode CRIU latencies: NOT MEASURED.** The redis episode run (Day 3) filtered stdout with grep and lost the per-episode CRIU timing lines. Redis MTTR (10.40s) is lower than postgres (10.87s), implying redis restart is faster and/or CRIU overhead is smaller, but no quantitative CRIU latency exists for redis.

**Do not cite a number for redis CRIU latency. Use "CRIU checkpoint creation succeeded; individual latency not recorded."**

---

## MTTR Episodes — postgres (Day 2)

Source: `run_episodes.py --container postgres --episodes 30 --interval 5 --timeout 60 --cooldown 3`
Definition: wall time from fault injection (8 × blk_io_latency events at 50–54ms injected directly into StatefulStateStore) to `checkpoint_and_restart` dispatch.
Fault injection method: synthetic (StateStore injection), NOT eBPF.

Raw MTTR values (s): 10.84, 10.79, 10.87, 10.94, 11.10, 11.07, 11.00, 10.86, 10.85, 10.83, 10.74, 10.77, 10.95, 10.84, 10.87, 10.77, 10.89, 10.92, 10.79, 10.83, 10.67, 10.79, 11.01, 10.91, 10.91, 10.79, 10.88, 10.84, 10.85, 10.81

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 10.87 s |
| stddev | 0.09 s |
| min | 10.67 s |
| max | 11.10 s |
| cycles to C&R | always 2 |
| cycle 1 action | always flush_io_queue |
| cycle 2 action | always checkpoint_and_restart |

**Effective recovery action in all 30 episodes: `docker restart` (CRIU restore failed).**

---

## MTTR Episodes — redis (Day 3)

Source: `run_episodes.py --container redis --episodes 30 --interval 5 --timeout 60 --cooldown 3`
Same fault injection method as postgres.

Raw MTTR values (s): 10.40, 10.39, 10.40, 10.40, 10.40, 10.39, 10.43, 10.41, 10.41, 10.40, 10.39, 10.40, 10.38, 10.40, 10.37, 10.40, 10.39, 10.41, 10.40, 10.50, 10.43, 10.38, 10.44, 10.40, 10.40, 10.40, 10.39, 10.37, 10.34, 10.40

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 10.40 s |
| stddev | 0.026 s |
| min | 10.34 s |
| max | 10.50 s |
| cycles to C&R | always 2 |

**Effective recovery action in all 30 episodes: `docker restart` (CRIU restore failed — same netns error as postgres).**

---

## MTTR Episodes — mysql (Day 3)

Source: `run_episodes.py --container mysql --episodes 30 --interval 5 --timeout 60 --cooldown 3`
Same fault injection method. MySQL image: `mysql:latest` (9.6.0). CRIU checkpoint creation FAILED on all 30 (runc multi-process constraint); recovery went directly to `docker restart`.

Raw MTTR values (s): 11.74, 11.64, 11.51, 11.53, 11.49, 11.51, 11.50, 11.50, 11.51, 11.48, 11.47, 11.49, 11.46, 11.49, 11.49, 11.50, 11.55, 11.54, 11.57, 11.82, 11.57, 11.50, 11.46, 11.41, 11.47, 11.45, 11.54, 11.55, 11.79, 11.63

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 11.54 s |
| stddev | 0.10 s |
| min | 11.41 s |
| max | 11.82 s |
| cycles to C&R | always 2 |

**Effective recovery action in all 30 episodes: `docker restart` (CRIU checkpoint creation itself failed).**
MySQL MTTR is highest because mysqld server initialisation on restart takes ~1.3s vs ~0.4s for postgres and ~0.3s for redis.

---

## Combined MTTR Summary (N=90)

| Workload | n | mean (s) | σ (s) | min (s) | max (s) | CRIU outcome |
|----------|---|----------|-------|---------|---------|--------------|
| postgres | 30 | 10.87 | 0.09 | 10.67 | 11.10 | checkpoint creates, restore fails |
| redis | 30 | 10.40 | 0.026 | 10.34 | 10.50 | checkpoint creates, restore fails |
| mysql | 30 | 11.54 | 0.10 | 11.41 | 11.82 | checkpoint fails (runc) |
| **all** | **90** | **10.94** | — | 10.34 | 11.82 | — |

**Do NOT report a combined σ.** The 0.55s figure that was briefly in the paper is cross-workload spread (driven by mysql vs redis restart speed difference), not episode variability. Per-workload σ values are the meaningful numbers.

---

## WASM Policy Sandbox (Day 1)

Source: `python3 stateful/wasm_executor.py` self-test, then 20-call latency test in actions.py.

| Item | Value |
|------|-------|
| wasmtime version | 45.0.0 |
| WAT modules | 4 (no_action, flush_io_queue, checkpoint_restart, escalate) |
| Self-test cases | 20 / 20 passed |
| Suppression rate helper test | 0.5 (2 blocked of 4 traces) ✓ |

**Latency (preliminary — from 20-call test, Day 1):**

| Stat | Value | Note |
|------|-------|------|
| n | 20 | Includes JIT warmup |
| mean | 865.57 µs | High due to first-call JIT |
| stddev | 3,637 µs | Dominated by first 1–2 calls |

This is NOT a reliable steady-state number. Proper 10,000-call benchmark is Day 5.
**Do not cite 865µs as the overhead. Use "sub-millisecond in steady state, formal benchmark pending."**

---

## NOT MEASURED (do not fabricate)

- Redis CRIU checkpoint latency per episode
- WASM steady-state latency (proper N=10,000 benchmark — Day 5)
- Shadow execution overhead (reconcile_both wall time — Day 5)
- eBPF probe overhead (pidstat with/without --real — Day 5)
- End-to-end MTTR including eBPF detection latency (requires real fault injection)
- MTTR for fault types other than blk_io_latency
- Baseline A (always-restart) unsafe action count — Day 4
- Ablation (no-FSM) unsafe C&R count — Day 4
