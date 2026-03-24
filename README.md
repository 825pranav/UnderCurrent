# UnderCurrent

**A Risk-Aware Autonomic Control Plane** — an experimental system that monitors containerised workloads at the kernel level, scores failure risk in real time, and automatically decides whether to restart or reschedule a container — all without a human in the loop.

---

## How it works

Every reconcile cycle runs **two parallel paths**:

```
Kernel events (eBPF / simulated)
        │
        ▼
  EventListener ──▶ StateStore (60s window) ──▶ Confidence Scorer
                                                       │
                                                 score (0.0–1.0)
                                                       │
                                                       ▼
                                               Reconcile / Decision DAG
                                              /                         \
                                        [real path]               [shadow path]
                                             │                         │
                                         Actions                  dry-run log
                                      (execute)                  (skipped)
                                             └────────────┬────────────┘
                                                          ▼
                                                  TraceLogger
                                              traces.jsonl
                                         (both real + shadow entries)
```

- **Real path** — decisions are computed AND executed; written to `traces.jsonl` with `"mode": "real"`
- **Shadow path** — decisions are computed but never executed; written to `traces.jsonl` with `"mode": "shadow"`
- After each cycle a side-by-side summary is printed; any difference between real and shadow is flagged as `*** DIVERGENCE ***`

### Stateless decision logic

| Confidence score | Action |
|---|---|
| `< 0.80` | `no_action` |
| `0.80 – 0.94` | `restart` (`docker restart <container>`) |
| `≥ 0.95` | `reschedule` (simulated) |

### Stateful decision logic (FSM-gated)

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
├── stateless/              ← stateless control plane (Type S)
│   ├── main.py             S8  orchestrator — wires the full pipeline
│   ├── event_listener.py   S1  eBPF kernel event capture
│   ├── state_store.py      S3  60s sliding-window failure tracker
│   ├── confidence.py       S4  risk scorer (0.0–1.0)
│   ├── reconcile.py        S5  decision engine + shadow controller
│   ├── actions.py          S6  action executor (restart / reschedule)
│   ├── trace_logger.py     S7  append-only JSON-line audit log
│   ├── dashboard.py            Rich terminal live dashboard
│   ├── streamlit_dashboard.py  Streamlit web dashboard
│   └── traces.jsonl             auto-created at runtime
├── stateful/               ← stateful FSM-gated control plane (Type F)
│   ├── main.py             F8  orchestrator
│   ├── event_listener.py   F1  eBPF probes (blk_io, vfs, mount)
│   ├── state_store.py      F3  stateful sliding-window store
│   ├── confidence.py       F4  I/O-aware risk scorer
│   ├── fsm.py              F5a ContainerFSM (Healthy→Degraded→…→Healthy)
│   ├── reconcile.py        F5b FSM-gated decision engine + shadow controller
│   ├── actions.py          F6  action executor (flush / checkpoint / escalate)
│   ├── trace_logger.py     F7  append-only JSON-line audit log
│   ├── dashboard.py            Rich terminal live dashboard
│   ├── streamlit_dashboard.py  Streamlit web dashboard
│   └── traces.jsonl             auto-created at runtime
├── shared/
│   └── trace_schema.py         versioned trace schema contract (v1.1.0)
└── README.md
```

---

## Quickstart

### Simulate mode (no root, no BPF — works on any machine)

```bash
git clone https://github.com/825pranav/UnderCurrent.git
cd UnderCurrent
```

**Stateless track:**
```bash
cd stateless
python3 main.py                  # both real + shadow paths every cycle
python3 main.py --shadow         # shadow-only (dry-run, no actions fired)
python3 main.py --interval 10    # reconcile every 10s (default: 5s)
python3 main.py --rate 2         # one synthetic event every ~2s (default: 1.5s)
```

**Stateful track:**
```bash
cd stateful
python3 main.py                  # both real + shadow paths + divergence tracking
python3 main.py --shadow         # shadow-only (uses throwaway FSM)
python3 main.py --interval 10
```

**Read the audit log:**
```bash
cat stateless/traces.jsonl | python3 -m json.tool | head -60
cat stateful/traces.jsonl  | python3 -m json.tool | head -60
```

---

---

### Real mode (Linux only — requires root and BPF)

**Prerequisites:**

```bash
# Ubuntu / Debian
sudo apt-get install bpfcc-tools python3-bpfcc linux-headers-$(uname -r)

# Fedora / RHEL
sudo dnf install bcc bcc-tools python3-bcc kernel-devel
```

**Run:**

```bash
sudo python3 main.py --real
sudo python3 main.py --real --shadow   # dry-run: observe without acting
```

> Real mode attaches eBPF probes to `sched_process_exit` and `tcp_connect` kernel tracepoints. Root is required.

---

## Dashboards

### Terminal dashboard (Rich)

```bash
pip install rich
python3 stateless/dashboard.py
```

Live-updating terminal UI — container risk table, action counters, live event feed. No browser needed. Best for live demos.

### Web dashboard (Streamlit)

Run the pipeline in one terminal, open the dashboard in another:

```bash
# Terminal 1 — generate traces
python3 stateless/main.py

# Terminal 2 — open web dashboard
pip install streamlit pandas plotly
streamlit run stateless/streamlit_dashboard.py
```

Opens at `http://localhost:8501` — confidence score charts, action distribution, per-container risk cards, filterable audit log. Auto-refreshes every 4 seconds.

---

## Reading the audit log

Every decision is appended to `stateless/traces.jsonl`:

```jsonc
{
  "trace_time": 1774186710.5,
  "container": "nginx",
  "score": 0.95,
  "action": "reschedule",
  "mode": "real",
  "why": "score 0.95 >= reschedule threshold 0.95",
  "executed": true,
  "stdout": "SIMULATED: would reschedule nginx via scheduler",
  "stderr": "",
  "decision_timestamp": 1774186710.1,
  "action_timestamp": 1774186710.4
}
```

---

## Module reference

| File | Role | Key API |
|---|---|---|
| `event_listener.py` | eBPF kernel probe | `EventListener().listen()` |
| `state_store.py` | Sliding-window event store | `record()`, `get_failures()`, `summary()` |
| `confidence.py` | Risk scorer | `compute_confidence(container, store)` |
| `reconcile.py` | Decision DAG | `reconcile(store, shadow=False)` |
| `actions.py` | Action executor | `execute(decision)` |
| `trace_logger.py` | Audit log | `log_decision(decision, result)` |
| `main.py` | Orchestrator | `python3 main.py [--real] [--shadow]` |

---

## Requirements

- **Simulate mode:** Python 3.8+ — no external packages
- **Real mode:** Linux kernel 4.9+, root, `bcc` installed as a system package (see above)
- **Docker actions:** Docker daemon running (for `docker restart`)

---

## License

MIT — see [LICENSE](LICENSE)
