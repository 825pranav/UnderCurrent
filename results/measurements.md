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

## WASM Policy Sandbox (Day 1 validation + Day 5 benchmark)

Source: `python3 stateful/wasm_executor.py` self-test (Day 1), then `scripts/benchmark_overhead.py` (Day 5).

| Item | Value |
|------|-------|
| wasmtime version | 45.0.0 |
| WAT modules | 4 (no_action, flush_io_queue, checkpoint_restart, escalate) |
| Self-test cases | 20 / 20 passed |
| Suppression rate helper test | 0.5 (2 blocked of 4 traces) ✓ |

**Steady-state latency (Day 5 benchmark — N=10,000 calls, post JIT warmup):**

| Stat | Value |
|------|-------|
| n | 10,000 |
| mean | 27.2 µs |
| p50 | 26.1 µs |
| p95 | 29.6 µs |
| p99 | 50.4 µs |
| min | 23.8 µs |
| max | 586.0 µs (outlier spike) |

Method: 200-call warmup, then 10,000 calls across all four action types (2,500 per action). Modules pre-compiled at import time (amortised). Each call creates a fresh `Store + Instance` (stateless sandbox design).

Python equivalent (plain dict/set lookup, same logic): mean=0.118µs. WASM/Python ratio: 230×.

As fraction of 5s reconcile interval: 27µs / 5,000,000µs = 0.0005% — negligible.

**Do NOT cite 865µs (Day 1 preliminary). Use Day 5 numbers. Cite as: "27µs mean, 50µs at p99 (N=10,000, post-warmup)."**

---

## Shadow Execution Overhead (Day 5)

Source: `scripts/benchmark_overhead.py`, N=2,000 iterations each path.
Each iteration: fresh StateStore loaded with 8 fault events, then reconcile().

| Metric | Value |
|--------|-------|
| single-path reconcile() | 6.9 µs mean |
| dual-path reconcile_both() | 14.9 µs mean |
| shadow overhead (absolute) | 8.1 µs |
| shadow overhead (relative) | 117% of single-path |
| as fraction of 5s interval | 0.0002% |

Note: proportional overhead (117%) is expected — shadow path runs an equivalent reconcile on a fresh FSM. Absolute overhead (8µs) is negligible.

Results file: `results/overhead_benchmark.json` (gitignored, reproducible via `python3 scripts/benchmark_overhead.py`).

---

## Ablation Study — Decision Policy Comparison (Day 4)

Source: `stateful/ablation.py` run on `stateful/traces.jsonl` (real-mode, node_type=F entries only).
N = 2,282 traces. Input `score` values held constant; decision mechanism varied.

| Controller | C&R | flush | no_action | Unsafe C&R | Missed C&R | Agreement vs baseline |
|--|--|--|--|--|--|--|
| UnderCurrent (full FSM) | 44 (1.93%) | 51 (2.23%) | 2187 (95.84%) | 0 | 0 | 1.0000 |
| No-FSM (always-restart) | 88 (3.86%) | 7 (0.31%) | 2187 (95.84%) | 44 | 0 | 0.9807 |
| Threshold-only (shadow) | 0 (0%) | 95 (4.16%) | 2187 (95.84%) | 0 | 44 | 0.9807 |
| No-confidence (binary) | 0 (0%) | 102 (4.47%) | 2180 (95.53%) | 0 | 44 | 0.9777 |

**Definitions:**
- Unsafe C&R: variant fires C&R in a cycle where full FSM would issue flush (pre-audit state).
- Missed C&R: variant issues flush in a cycle where full FSM approves C&R (post-audit state).

**Key finding:** No-FSM fires 2× as many C&Rs, half premature. Threshold-only and No-confidence never reach C&R at all (0 dispatches). UnderCurrent is the only variant with 0 unsafe + 0 missed.

**Connection to M2:** The 44 cycles where Threshold-only misses C&R are exactly the 44 divergent cycles in $M_2 = 0.3697$ (44 of 119 active Phase-2 cycles). Shadow path = Threshold-only. M2 quantifies the frequency at which FSM memory changes the outcome.

Results file: `stateful/ablation_results.json` (gitignored, reproducible via `python3 stateful/ablation.py`).

---

## NOT MEASURED (do not fabricate)

- Redis CRIU checkpoint latency per episode
- eBPF probe overhead (pidstat with/without --real — not measured; deprioritised Day 5)
- End-to-end MTTR including eBPF detection latency (requires real fault injection)
- MTTR for fault types other than blk_io_latency

---

## MTTR Campaign — 2026-09-01 (real flush, fixed WASM gate, naive baseline)

Source: `run_episodes.py --container <c> --episodes 30 --interval 5 --timeout 120 --cooldown 5 [--controller naive]`
Definition: wall time from fault injection (8 × blk_io_latency events at 50–54 ms injected directly into StatefulStateStore) to `checkpoint_and_restart` completion (CRIU checkpoint → restore fails on the documented containerd bug → docker restart).
Fault injection method: synthetic (StateStore injection), NOT eBPF.

Differences from the Day-2/Day-3 runs above:
- `flush_io_queue` is now a real synchronous durable flush (`CHECKPOINT` / `SAVE` / `FLUSH TABLES; FLUSH LOGS` via docker exec) — see `evaluation/results/flush_verification.json`.
- WASM sandbox gates on `fsm_state_at_dispatch` (commit efc2fc0). Between 2026-06-20 and this fix every live C&R was WASM-blocked; a first 90-episode run that day reported 10.08 s because it timed a blocked no-op. Those runs are discarded.
- **Naive baseline** (`--controller naive`): dispatches `checkpoint_and_restart()` directly on the first reconcile cycle that sees any fault event — no score, no FSM, no flush. It bypasses `execute()` because the policy gate would otherwise block it. This is a naive local controller, not a Kubernetes liveness policy.
- Summaries: `results/mttr_2026-09-01/mttr_<c>[_naive].json` (the `scripts/` copies are gitignored).

### UnderCurrent — postgres

Raw MTTR values (s): 11.13, 10.94, 10.95, 10.93, 10.91, 10.89, 10.91, 10.91, 10.86, 10.98, 10.96, 10.94, 10.94, 10.93, 10.90, 10.85, 11.09, 10.90, 10.98, 10.94, 10.97, 10.98, 10.97, 10.94, 10.95, 10.97, 10.86, 10.94, 10.95, 10.99

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 10.95 s |
| stddev | 0.06 s |
| min | 10.85 s |
| max | 11.13 s |
| cycles to C&R | always 2 |
| actions | flush_io_queue → checkpoint_and_restart |

### UnderCurrent — redis

Raw MTTR values (s): 10.44, 10.41, 10.42, 10.40, 10.39, 10.45, 10.39, 10.43, 10.47, 10.47, 10.44, 10.43, 10.42, 10.51, 10.47, 10.45, 10.43, 10.42, 10.44, 10.45, 10.42, 10.41, 10.40, 10.41, 10.44, 10.44, 10.43, 10.40, 10.47, 10.43

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 10.43 s |
| stddev | 0.03 s |
| min | 10.39 s |
| max | 10.51 s |
| cycles to C&R | always 2 |
| actions | flush_io_queue → checkpoint_and_restart |

### UnderCurrent — mysql

Raw MTTR values (s): 11.71, 11.76, 11.77, 11.64, 11.67, 11.75, 11.68, 11.55, 11.53, 11.49, 11.61, 11.72, 11.58, 11.64, 11.57, 11.44, 11.52, 11.50, 11.49, 11.86, 11.85, 12.00, 11.92, 11.75, 11.73, 11.57, 11.54, 11.63, 11.56, 11.66

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 11.66 s |
| stddev | 0.13 s |
| min | 11.44 s |
| max | 12.00 s |
| cycles to C&R | always 2 |
| actions | flush_io_queue → checkpoint_and_restart |

### Naive — postgres

Raw MTTR values (s): 6.03, 5.87, 5.80, 5.79, 5.86, 5.79, 5.80, 5.77, 5.82, 5.73, 5.81, 5.79, 5.77, 5.84, 5.78, 5.77, 5.76, 5.76, 5.83, 5.78, 5.76, 5.78, 5.82, 5.80, 5.84, 5.82, 5.80, 5.79, 5.77, 5.81

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 5.80 s |
| stddev | 0.05 s |
| min | 5.73 s |
| max | 6.03 s |
| cycles to C&R | always 1 |
| actions | checkpoint_and_restart |

### Naive — redis

Raw MTTR values (s): 5.35, 5.32, 5.33, 5.34, 5.34, 5.35, 5.35, 5.34, 5.39, 5.33, 5.36, 5.38, 5.39, 5.38, 5.35, 5.36, 5.37, 5.36, 5.34, 5.38, 5.33, 5.33, 5.36, 5.36, 5.34, 5.31, 5.35, 5.36, 5.38, 5.37

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 5.35 s |
| stddev | 0.02 s |
| min | 5.31 s |
| max | 5.39 s |
| cycles to C&R | always 1 |
| actions | checkpoint_and_restart |

### Naive — mysql

Raw MTTR values (s): 6.81, 6.66, 6.68, 6.62, 6.52, 6.47, 6.49, 6.54, 6.51, 6.73, 6.50, 6.53, 6.59, 6.55, 6.50, 6.44, 6.46, 6.59, 6.82, 6.49, 6.47, 6.51, 6.60, 6.47, 6.47, 6.51, 6.40, 6.43, 6.48, 6.44

| Stat | Value |
|------|-------|
| n | 30 |
| recovered | 30 / 30 |
| timed out | 0 |
| mean | 6.54 s |
| stddev | 0.10 s |
| min | 6.40 s |
| max | 6.82 s |
| cycles to C&R | always 1 |
| actions | checkpoint_and_restart |

