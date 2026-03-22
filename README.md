# UnderCurrent

**A Risk-Aware Autonomic Control Plane** — an experimental system that monitors containerised workloads at the kernel level, scores failure risk in real time, and automatically decides whether to restart or reschedule a container — all without a human in the loop.

---

## How it works

```
┌─────────────────────────────────────────────────────────────────┐
│                        UnderCurrent                             │
│                                                                 │
│  Kernel events                                                  │
│  (eBPF / simulated)                                             │
│        │                                                        │
│        ▼                                                        │
│  ┌──────────────┐     ┌──────────────┐     ┌────────────────┐  │
│  │ EventListener│────▶│  StateStore  │────▶│  Confidence    │  │
│  │  (S1–S2)     │     │  60s window  │     │  Scorer (S4)   │  │
│  └──────────────┘     └──────────────┘     └───────┬────────┘  │
│                                                    │           │
│                                              score (0.0–1.0)   │
│                                                    │           │
│                                                    ▼           │
│                                          ┌─────────────────┐   │
│                                          │   Reconcile     │   │
│                                          │  Decision DAG   │   │
│                                          │     (S5)        │   │
│                                          └────────┬────────┘   │
│                                                   │            │
│                              ┌────────────────────┤            │
│                              │                    │            │
│                         [real path]         [shadow path]      │
│                              │                    │            │
│                              ▼                    ▼            │
│                       ┌────────────┐      ┌────────────────┐   │
│                       │  Actions   │      │  Shadow log    │   │
│                       │  (S6)      │      │  (dry-run)     │   │
│                       └─────┬──────┘      └───────┬────────┘   │
│                             │                     │            │
│                             └──────────┬──────────┘            │
│                                        ▼                       │
│                                ┌──────────────┐                │
│                                │ TraceLogger  │                │
│                                │ traces.jsonl │                │
│                                └──────────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

### Decision logic

| Confidence score | Action taken |
|---|---|
| `< 0.80` | no action |
| `0.80 – 0.94` | `docker restart <container>` |
| `≥ 0.95` | reschedule (simulated) |

Every decision — real or shadow — is appended to `stateless/traces.jsonl` with a human-readable `why` field for explainability.

---

## Repo structure

```
UnderCurrent/
├── stateless/              ← autonomic control plane (this project)
│   ├── main.py             S8  orchestrator — wires the full pipeline
│   ├── event_listener.py   S1  eBPF kernel event capture
│   ├── state_store.py      S3  60s sliding-window failure tracker
│   ├── confidence.py       S4  risk scorer (0.0–1.0)
│   ├── reconcile.py        S5  decision engine + shadow controller
│   ├── actions.py          S6  action executor (restart / reschedule)
│   ├── trace_logger.py     S7  append-only JSON-line audit log
│   └── traces.jsonl             auto-created at runtime
├── stateful/               ← partner module (add your files here)
├── shared/                 ← shared utilities (cross-module helpers)
└── README.md
```

---

## Contributing — Stateful module

The `stateful/` folder is reserved for the stateful side of the control plane (developed separately).

To contribute:
1. Clone the repo: `git clone https://github.com/825pranav/UnderCurrent.git`
2. Add your files inside `stateful/`
3. If your code has pip dependencies, add them to `requirements.txt` under a `# STATEFUL` section
4. Open a PR or push directly to `main`

---

## Quickstart

### Simulate mode (no root, no BPF — works on any machine)

```bash
git clone https://github.com/825pranav/UnderCurrent.git
cd UnderCurrent/stateless
python3 main.py
```

This generates synthetic container events and runs the full pipeline locally. No extra dependencies needed.

**Options:**

```bash
python3 main.py --interval 10   # reconcile every 10s (default: 5s)
python3 main.py --rate 2        # one synthetic event every ~2s (default: 1.5s)
python3 main.py --shadow        # log decisions only, skip real actions
```

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
