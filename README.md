# UnderCurrent

**A Risk-Aware Autonomic Control Plane** — monitors containerised workloads at the kernel level, scores failure risk in real time, and automatically triggers corrective actions without human intervention. Two parallel tracks: **Stateless (Type S)** and **Stateful (Type F)**.

**Authors:** Pranav Negi · Reema Sarkar

---

## How it works

Every reconcile cycle runs **two parallel paths**:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           UnderCurrent                                   │
│                                                                          │
│   ── Stateless Track (Type S) ──────────────────────────────────────     │
│                                                                          │
│   Kernel events          StateStore        Confidence                    │
│   (eBPF / simulated) ──▶ 60s window    ──▶ Scorer (0.0–1.0)             │
│                                                    │                     │
│                                          ┌─────────▼──────────┐         │
│                                          │  Reconcile DAG      │         │
│                                          │  restart/reschedule │         │
│                                          └──────┬──────┬───────┘         │
│                                           [real]│  [shadow]              │
│                                                 ▼      ▼                 │
│                                          Actions   Shadow log            │
│                                                 │                        │
│                                          stateless/traces.jsonl          │
│                                                                          │
│   ── Stateful Track (Type F) ───────────────────────────────────────     │
│                                                                          │
│   Kernel events          StatefulStateStore   Confidence                 │
│   (I/O, volume, net) ──▶ 60s window       ──▶ Scorer (I/O signals)      │
│                                                    │                     │
│                                          ┌─────────▼──────────┐         │
│                                          │  FSM-Gated Reconcile│         │
│                                          │  flush/checkpoint/  │         │
│                                          │  escalate           │         │
│                                          └──────┬──────┬───────┘         │
│                                           [real]│  [shadow]              │
│                                                 ▼      ▼                 │
│                                          Actions   Shadow log            │
│                                                 │                        │
│                                          stateful/traces.jsonl           │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Real path** — decisions are computed AND executed; written to `traces.jsonl` with `"mode": "real"`
- **Shadow path** — decisions are computed but never executed; written to `traces.jsonl` with `"mode": "shadow"`
- After each cycle a side-by-side summary is printed; any difference between real and shadow is flagged as `[DIVERGENCE]`

---

## Decision logic

### Stateless (Type S)

| Confidence score | Action |
|---|---|
| `< 0.80` | `no_action` |
| `0.80 – 0.94` | `restart` (`docker restart <container>`) |
| `≥ 0.95` | `reschedule` (simulated) |

### Stateful (Type F) — FSM-gated

| Confidence score | Raw action | FSM gate |
|---|---|---|
| `< 0.50` | `no_action` | — |
| `0.50 – 0.79` | `flush_io_queue` | reversible, always allowed |
| `0.80 – 0.94` | `checkpoint_and_restart` | only from `Audited` state |
| `≥ 0.95` | `escalate` | only from `Degraded/Audited/Repairing` |

FSM lifecycle: `Healthy → Degraded → Audited → Repairing → Recovered → Healthy`

The shadow path uses a **fresh FSM** each cycle, so it diverges from the real FSM over time — this divergence is a tracked research metric (`[DIVERGENCE]` log lines).

---

## Repo structure

```
UnderCurrent/
├── stateless/                   ← Type S control plane
│   ├── main.py                  orchestrator — full pipeline
│   ├── event_listener.py        eBPF kernel event capture
│   ├── state_store.py           60s sliding-window failure tracker
│   ├── confidence.py            risk scorer (0.0–1.0)
│   ├── reconcile.py             decision DAG + shadow controller
│   ├── actions.py               restart / reschedule executor
│   └── trace_logger.py          append-only JSON-line audit log
│
├── stateful/                    ← Type F control plane
│   ├── main.py                  orchestrator — full pipeline
│   ├── event_listener.py        eBPF kernel event capture (I/O + volume signals)
│   ├── state_store.py           StatefulStateStore — extended event schema
│   ├── fsm.py                   ContainerFSM — formal state machine enforcement
│   ├── confidence.py            risk scorer (I/O latency + volume signals)
│   ├── reconcile.py             FSM-gated decision engine + shadow controller
│   ├── actions.py               flush / checkpoint_restart / escalate executor
│   ├── trace_logger.py          append-only JSON-line audit log (extended schema)
│   ├── wasm_executor.py         WASM policy sandbox
│   └── wasm/                    WAT policy modules (no_action, flush, checkpoint, escalate)
│
├── shared/                      ← cross-track shared contracts and evaluation
│   ├── trace_schema.py          field catalogue + schema version (1.3.0)
│   ├── metrics.py               all 6 research metrics
│   ├── eval_runner.py           compute metrics from trace files
│   ├── verify_safety_gates.py   design claim verification
│   ├── cross_eval.py            cross-controller comparison harness
│   └── sensitivity.py           threshold sweep analysis
│
├── evaluation/                  ← reproducible evaluation suite
│   ├── fault_harness.py         fault scenario definitions + runner
│   ├── baseline_controller.py   naive threshold baseline for comparison
│   ├── run_trials.py            multi-trial experiment runner
│   ├── compute_metrics.py       confusion matrix + pairwise statistical tests
│   ├── distribution_analysis.py MTTR distribution analysis
│   ├── ablation.py              ablation study (FSM / confidence variants)
│   ├── sensitivity.py           threshold sensitivity sweep
│   └── results/                 pre-computed CSV result files
│
├── integration/                 ← unified launcher and dashboard
│   ├── launcher.py              start both pipelines + dashboard (supervised)
│   └── unified_dashboard.py     Streamlit view of both tracks
│
├── dashboard/                   ← React dashboard
│   ├── backend.py               Flask API (http://localhost:5050)
│   └── frontend/                Vite + React + Tailwind
│
├── run.py                       entry point — real mode
├── shadow.py                    entry point — shadow / dry-run mode
├── streamlit_dash.py            entry point — Streamlit unified dashboard
├── requirements.txt
└── README.md
```

---

## Quickstart

### Run everything (recommended)

```bash
git clone https://github.com/825pranav/UnderCurrent.git
cd UnderCurrent
pip install streamlit pandas plotly wasmtime
python3 run.py
```

Opens both pipelines + React dashboard at `http://localhost:5050`.  
For the Streamlit view instead: `python3 streamlit_dash.py`

---

### Reproduce paper evaluation metrics

The Phase 2 evaluation snapshot (stateful + stateless traces, divergence log) is committed in `data/phase2/`. To reproduce all six metrics reported in the paper:

```bash
# Reproduce paper Phase 2 metrics (M1–M2, M4–M6):
python3 shared/eval_runner.py --data-dir data/phase2

# Reproduce trial-based accuracy, ablation, and sensitivity (M3 and functional tests):
python3 evaluation/run_trials.py        # N=30 trials per scenario
python3 evaluation/sensitivity.py       # threshold sensitivity sweep
python3 evaluation/ablation.py          # ablation study

# Regenerate paper figures:
python3 figures/generate_figures.py
```

Note: `stateful/traces.jsonl` and `stateless/traces.jsonl` are **not committed** (gitignored) — they grow as the system runs. Use `--data-dir data/phase2` to compute metrics from the frozen snapshot used in the paper.

---

### Run tracks independently

**Stateless only:**
```bash
cd stateless
python3 main.py                      # real mode, simulated events
python3 main.py --mode shadow        # shadow-only (dry-run, no actions fired)
python3 main.py --mode divergence    # both paths, log disagreements
python3 main.py --interval 10        # reconcile every 10s (default: 5s)
python3 main.py --rate 2             # one synthetic event every ~2s (default: 1.5s)
```

**Stateful only:**
```bash
cd stateful
python3 main.py                      # real mode, simulated events
python3 main.py --mode shadow        # shadow-only (uses throwaway FSM)
python3 main.py --mode divergence    # both paths, log FSM disagreements
python3 main.py --interval 10
```

**Read the audit log:**
```bash
cat stateless/traces.jsonl | python3 -m json.tool | head -60
cat stateful/traces.jsonl  | python3 -m json.tool | head -60
```

---

### Real eBPF mode (Linux only — requires root)

```bash
# Ubuntu / Debian
sudo apt-get install bpfcc-tools python3-bpfcc linux-headers-$(uname -r)

sudo python3 stateless/main.py --real
sudo python3 stateful/main.py  --real
```

---

## Dashboards

### React dashboard (recommended)
```bash
python3 run.py
# http://localhost:5050
```
Full-featured dark UI — Overview, Stateless, Stateful, Shadow, and Audit Log tabs. Global mode filter (All / Real / Shadow), score timeline, action distribution, FSM state cards, divergence table, sortable/paginated audit log, CSV and PDF export.

### Streamlit unified dashboard
```bash
python3 streamlit_dash.py
# http://localhost:8501
```
Shows both tracks side-by-side — score timeline, action distribution per track, FSM state panel, research metrics strip, unified audit log. Auto-refreshes every 4 seconds.

---

## Audit log format

Both tracks write to their own `traces.jsonl`. Base fields are shared; stateful traces carry additional fields.

**Base fields (both tracks):**
```jsonc
{
  "trace_time": 1774186710.5,
  "container": "nginx",
  "score": 0.95,
  "action": "reschedule",
  "mode": "real",
  "why": "score 0.95 >= reschedule threshold 0.95",
  "executed": true,
  "stdout": "...", "stderr": "",
  "decision_timestamp": 1774186710.1,
  "action_timestamp": 1774186710.4
}
```

**Additional fields in stateful traces:**
```jsonc
{
  "node_type": "F",
  "fsm_state": "Degraded",
  "fsm_transition": "Degraded→Audited",
  "reversibility": "reversible",
  "kernel_signals": ["blk_io_latency_high"],
  "dag_pattern": "io_degradation_major",
  "blocked_reason": null
}
```

Schema version: `1.3.0` — see `shared/trace_schema.py`

---

## Module reference

### Stateless (Type S)

| File | Role | Key API |
|---|---|---|
| `event_listener.py` | eBPF kernel probe | `EventListener().listen()` |
| `state_store.py` | Sliding-window store | `record()`, `get_failures()`, `summary()` |
| `confidence.py` | Risk scorer | `compute_confidence(container, store)` |
| `reconcile.py` | Decision DAG | `reconcile(store, shadow=False)` |
| `actions.py` | Action executor | `execute(decision)` |
| `trace_logger.py` | Audit log | `log_decision(decision, result)` |
| `main.py` | Orchestrator | `python3 main.py [--real] [--mode shadow]` |

### Stateful (Type F)

| File | Role | Key API |
|---|---|---|
| `event_listener.py` | eBPF — I/O + volume probes | `EventListener().listen()` |
| `state_store.py` | StatefulStateStore | `record()`, `get_failures()`, `summary()` |
| `fsm.py` | Container FSM | `ContainerFSM().apply(container, trigger)` |
| `confidence.py` | I/O risk scorer | `compute_confidence(container, store)` |
| `reconcile.py` | FSM-gated DAG | `reconcile(store, fsm, shadow=False)` |
| `actions.py` | flush / checkpoint / escalate | `execute(decision)` |
| `trace_logger.py` | Extended audit log | `log_decision(decision, result)` |
| `main.py` | Orchestrator | `python3 main.py [--real] [--mode shadow\|divergence]` |

---

## Requirements

- **Simulate mode:** Python 3.8+ — no external packages
- **Dashboards:** `pip install streamlit pandas plotly`
- **Real eBPF mode:** Linux kernel 4.9+, root, `bcc` system package
- **Docker actions:** Docker daemon running

See `requirements.txt` for full details.

---

## License

MIT — see [LICENSE](LICENSE)
