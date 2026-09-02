# UnderCurrent — Handoff v4 (2026-09-02)

Supersedes HANDOFF_v3.md. Everything below was verified on the VM.
Branch `fixes`, 15+ commits since `60721d3`, **not yet pushed** (see §6).

Path F was chosen on 2026-09-01 ("make it true"). It is done except for the
paper edits, which the author does. This file is the input to those edits.

---

## 1. Corpus metrics — the substitution table

`data/phase3/` is the new committed corpus. Numbers come from
`scripts/finalize_corpus.py` (writes `data/phase3/corpus_summary.json`,
`metrics.json`, `ablation_results.json`; prints the April comparison).


| Item | April (`data/phase2`) | New (`data/phase3`) |
|---|---|---|
| total traces (real+shadow, both tracks) | 9,132 | 27,592 |
| N_real^F / shadow | 2,282 / 2,282 | 6,735 / 6,735 |
| N_real^S / shadow | 2,284 / 2,284 | 7,061 / 7,061 |
| containers in Type-F corpus | postgres only | postgres, redis, mysql, nginx |
| WASM active in corpus | no (Python fallback) | yes (0 fallback lines) |
| active Type-F (flush / C&R) | 95 (51 / 44) | 670 (351 / 319) |
| active Type-S (reschedule) | 2,113 | 2,274 |
| divergence records (F) | 44 | 319 (all C&R→flush) |
| M1 | 0.0000 | 0.0000 |
| M2 S / F / overall | 0.0000 / 0.0193 / 0.0096 | 0.0000 / 0.0474 (319/6,735) / 0.0231 (319/13,796) |
| M4 | 1.0000 | 1.0000 |
| M5 | 1.0000 | 1.0000 |
| M6 S / F / overall | 0.0000 / 0.0193 / 0.0096 | 0.0000 / 0.0466 (314 FSM deferrals) / 0.0228 |
| C&R per container | postgres 44 | postgres 136, redis 106, mysql 77, nginx 0 |
| corpus sha256 stateful / stateless (16) | `fad58176d1926963` | `e7823e71bc457c1c` / see corpus_summary.json |
| Table III (ablation replay) | N=2,282 | N=6,735 — see below |

**Table III on the new corpus** (`data/phase3/ablation_results.json`, scores
fixed, decision mechanism varies, N = 6,735 real Type-F):

| Controller | C&R dispatched | Unsafe C&R (premature) | Missed C&R |
|---|---|---|---|
| Proposed (full FSM) | 319 (4.74%) | 0 | 0 |
| No-FSM (always-restart) | 633 (9.40%) | 314 | 0 |
| Threshold-only (shadow) | 0 | 0 | 319 |
| No-confidence (binary) | 0 | 0 | 319 (+77 extra flushes; 396 disagreements) |

Other corpus facts for §VII: FSM state distribution of real Type-F records
see `data/phase3/metrics.json` (`fsm_state_distribution_stateful_real`) (Audited is a dead end — §6);
kernel signals observed: F = blk_io_latency_high, blk_io_latency_normal,
vfs_write_error (no read errors, by design of the errno filter); S =
process_exit only (as in April; tcp_connect and restart branch still absent
from the corpus — the existing Limitation stands).

**M2 ≠ M6 in this corpus — the paper's "structural equality" paragraph must
be rewritten, not renumbered.** M6 counts deferral cycles (score ≥ τ_repair
while Degraded → C&R downgraded to flush); M2 counts C&R cycles (real FSM in
Audited dispatches C&R, memoryless shadow would flush). Exact decomposition:

| | count |
|---|---|
| deferral immediately followed by C&R (the paired case the paper describes) | 294 |
| deferral NOT followed by C&R (fault cleared before the next cycle) | 20 |
| C&R NOT preceded by a deferral (FSM was already Audited from an earlier slot) | 25 |
| M6 numerator = 294 + 20 | 314 |
| M2 numerator = 294 + 25 | 319 |

Both unpaired kinds have one cause: **Audited has no exit** (§6). A slot that
ends right after a deferral leaves the FSM in Audited; the next slot's first
score ≥ 0.80 then fires C&R with no deferral in front of it. April (one
container, sporadic faults) always completed the sequence to Recovered and
healed, so the pairing was exact there. Correct paper reading: the equality
is structural *per fault episode that starts from Healthy*; repeated faults
against a stale audit break it. Adding an Audited→Healthy transition on
fault clearance would restore it (author's decision).

**Ablation "314 premature C&R" = the 314 deferral cycles by construction**
(no-FSM fires wherever score ≥ 0.80 regardless of state; those are exactly
the cycles the real controller deferred). 319 = actual C&R count.
319 − 314 = 25 − 20.

**WASM blocked nothing in this corpus** (wasm_blocked = 0 on every record):
reconcile's FSM gate defers every ineligible C&R one cycle earlier, so the
sandbox never sees one. It was active throughout, and the June→September
gate bug (§4) proves it is a real enforcement point; but M6 counts FSM
deferrals, not WASM blocks — say so; footnote (b) goes.

**M1 / M4 / M5 are exact, not rounded:** 0 cross-track-contaminated records
of 27,592; 2,944 active real decisions, all 2,944 `reversible`; 0 records
with a missing/empty required explanation field.

## 2. MTTR — final (committed b34085f, `results/measurements.md`)

Real flush + fixed WASM gate, N=30 per workload, 0 timeouts, 0 WASM blocks:

| Workload | UnderCurrent μ (σ) | Naive baseline μ (σ) |
|---|---|---|
| postgres | 10.95 s (0.06) | 5.80 s (0.05) |
| redis | 10.43 s (0.03) | 5.35 s (0.02) |
| mysql | 11.66 s (0.13) | 6.54 s (0.10) |

Naive = `run_episodes.py --controller naive`: C&R on the first cycle with any
fault event, no score, no FSM, no flush; calls the action directly because
`execute()`'s gate would block it. Frame as a *local restart-on-signal
controller*, not a Kubernetes liveness policy. The ~5 s gap is one reconcile
interval = the flush-and-audit cycle.

A draft of the MTTR paper edits (paragraph, two-series pgfplots figure, Table
II row, footnote a) is in the session scratchpad as `paper_mttr_edits.patch`;
it was reverted from the tree at the author's request.

## 3. What is now true that was hollow in v3

1. **Flush is real.** `flush_io_queue()` runs `CHECKPOINT` / synchronous
   `SAVE` / `FLUSH TABLES; FLUSH LOGS` inside the container via docker exec
   and returns `flush_latency_ms` (30–120 ms). Gate:
   `scripts/verify_flush.py` → `evaluation/results/flush_verification.json`.
   Note: no SQL statement flushes the InnoDB buffer pool synchronously;
   MySQL durability is the fsync'd redo log, verified via `Innodb_data_fsyncs`.
2. **Four containers in the corpus**, both tracks.
3. **WASM active** for the whole corpus (launch under the PYTHONPATH wrapper).
4. **eBPF attribution is correct** (it was not in April — see §4).
5. **CRIU** unchanged: postgres/redis checkpoint creates then restore fails
   (Docker netns bind-mount, containerd#12141); mysql checkpoint creation
   fails (17 POSIX file locks, runc). Still a Limitation. Optional
   Podman demonstration (~2 h) would show CRIU restore works outside Docker.

## 4. Bugs found and fixed on the way (all are paper-relevant)

| Commit | Bug | Effect before the fix |
|---|---|---|
| efc2fc0 | `execute()` gated the WASM sandbox on `fsm_state_after` (Repairing); module allows C&R from Audited only | **Every live C&R WASM-blocked since 2026-06-20.** 90 MTTR episodes timed a no-op (10.08 s). Fixed via `fsm_state_at_dispatch` (schema 1.4.0). |
| 7401e57 | blk latency probe keyed by *current* pid at completion (IRQ context) | Block latencies attributed to whichever task was interrupted — random. Now keyed by request pointer, issuer captured at start. |
| 62846ce, 7401e57 | comm filter exact-match; mysqld does I/O on threads `ib_io_wr-N`, `ib_log_*`, `connection`; both `main.py` handlers bypassed the resolver | mysql produced **zero** attributed events. |
| 3837051 | vfs probes counted any negative return | EAGAIN/EINTR from non-blocking sockets scored 0.88 permanently → restart storm (~5 C&R/min, self-sustaining). Now storage errnos only (EIO, ENOSPC, EROFS, …). |
| cd79637 | failed `docker start --checkpoint` leaves `/tmp/ctrd-checkpoint*` (69 MB each) | 29 GB leaked in 8 h, disk hit the injector's floor. Purged after each failed restore. |
| 1df338d | injector cleanup raced the controller's C&R restart | mysql binlog +600 MB per slot. Cleanup waits for the server. |
| 069d8de | `run.py --real` forces `--mode real` → no shadow path | 8 h run of 2026-09-01 had **no divergence log** (M2 impossible). April ran `--mode divergence`; launcher now does too. |

## 5. Corpus provenance — say this in §VII-A

- Controller: `run.py --real --no-dashboard --mode divergence`, interval 5 s,
  window 60 s, wasmtime active, all four containers restarted at launch.
- Fault campaign (`scripts/fault_campaign.sh`): 60 s injection per container,
  120 s rest, rotation postgres → redis → mysql → nginx (12 min/cycle).
  Faults (`scripts/inject_fault_v2.sh`): postgres `COPY … TO '/dev/full'`
  (ENOSPC, vfs_write_error); redis synchronous `SAVE` of a ~50 MB RDB and
  mysql 60 KB-row autocommitted inserts, each **under a host-side direct-I/O
  noisy neighbour** (`dd oflag=direct`, comm `dd` is dropped by the filter)
  so the server's own bios exceed 10 ms (blk_io_latency_high); nginx worker
  kill (process_exit). The first ~6 min are the step-2 gate's injections.
- The corpus is a **controlled campaign**, fault-dominated by design; say so.
- Collected in **two campaign windows** on 2026-09-02 (UTC), one controller
  session: 08:08:40–09:38:18 (gate + 7 cycles) and 13:26:20–14:26:30
  (5 cycles); 12 cycles, 12 injection slots per container. The corpus is
  exactly those two windows (`finalize_corpus.py --windows …`, recorded in
  `data/phase3/corpus_summary.json`).
- `archive/phase3-superseded-20260902-142738/`: the 2026-09-01 21:44→05:45 run —
  4 containers, 22,564 real stateful records, but **no shadow path**; keep
  only as a reference, do not cite for M2/M6.

## 6. Two behaviours the paper does not yet discuss

1. **Restarts repeat while a fault persists.** Within a 60 s fault, C&R fires
   about every 10 s (flush → audit → C&R → restart → still faulting). April:
   44 C&R in 20 h; this corpus: hundreds. Reviewers will ask about backoff.
2. **Audited has no exit.** Table I has no transition out of Audited when
   the fault clears (only approve_repair or degrade), so containers sit in
   Audited through quiet time and a later fault goes straight to C&R with a
   stale audit. This is what breaks the M2 = M6 pairing (§1: 20 + 25
   unpaired cycles). Left as designed; author's call.

## 7. Still open

- **Paper edits** — DONE 2026-09-02 at the author's request (commit after c80d244): all numbers, naive baseline figure, M2/M6 paragraph, footnote b, Limitations additions, six pages under tectonic+TeX Gyre Termes. Verify the page count with a real pdflatex (Times) before submission; the Future Work figure was removed to fit. Previously: Abstract, §VII-A (fault methods, 4 containers,
  campaign framing), §VII-B numbers, Table II + footnotes (drop footnote b:
  WASM was active), §VII-C MTTR + naive baseline, Table III, Limitations
  (remove "corpus predates wasmtime"; add §6 items), Intro flush claim is
  now true as written.
- **LaTeX** — fixed in the same commit (were: three pre-existing errors blocking any real compile: lines 96 and
  251 `>=Stealth[…]` → `-{Stealth[…]}`; line 433 `draw color=black` →
  `error bar style={draw=black}`. `~/.local/bin/tectonic -X compile
  paper/uc.tex` then builds 7 pages (XeTeX; Times metrics differ from
  pdflatex — do a real pdflatex pass before submission).
- **Push** — `git push origin fixes`. The remote URL still embeds the GitHub
  PAT in plaintext (`.git/config`); rotate it and switch to a credential
  helper first.
- `stateless/divergence_log.jsonl` never exists (stateless track has no FSM,
  so it never diverges); metrics treat absence as 0, as in April.

## 8. Traps (v3 traps A–G still apply) plus new ones

- **H.** Never edit a script the campaign is executing (`inject_fault_v2.sh`)
  in place — bash reads it incrementally; write a temp file and `mv`.
- **I.** `pkill -f <pattern>` matches your own shell if the pattern appears
  in your command line; use `pgrep -f "patter[n]"` bracket trick.
- **J.** `sudo` tickets are per-tty: a `sudo -v` in the user's terminal does
  not help a process started elsewhere. The launcher keeps its own ticket
  alive; everything else in the campaign runs without sudo.
- **L.** The campaign log appends across runs; filter from the last
  `[campaign] start` line.

## 9. External review (2026-09-02) vs status after this work

| Review point | Status | Evidence / what remains |
|---|---|---|
| CRIU never succeeds | not solved; salvage argument now has evidence | flush is real → the gate provably orders a verified durable flush before every restart, CRIU or not. Paper must say it head-on. Podman demo optional. |
| WASM barely evaluated | solved in data | whole corpus wasmtime-active, 0 fallback. Caveat: sandbox blocked nothing (FSM defers first). Drop footnote (b). |
| No external baseline | partial | naive restart-on-signal controller on the same harness (§2). Not a prior-art tool. |
| Scale / generalizability | partial | 4 containers actively faulted, 3× corpus, 12 slots each; still one machine, one instance each. |
| Statistical thinness | partial | 319 divergences; 12 independent slots per container allow per-slot CIs (not yet computed). F1 framing untouched. |
| "MTTR" mislabel | partial | M3 now spans detection → completed docker restart with a real flush; still not service readiness. Rename/qualify. |
| "measurably necessary", safety vs security, kernel-version cites | not solved (paper) | wording. |
| ablation.py stale path | moot | finalize passes the corpus path explicitly. |
| new: restarts repeat every ~10 s during a persistent fault; Audited dead end | new, from this corpus | §6. |
