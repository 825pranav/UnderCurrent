# streamlit_dashboard.py — F10: Stateful Web Dashboard
#
# Live web UI for the stateful control plane — reads stateful/traces.jsonl.
# Shows FSM state history, I/O risk timeline, action distribution,
# per-container FSM cards, divergence metrics, and full audit log.
#
# Install:  pip install streamlit pandas plotly
# Run:
#   Terminal 1 — generate traces:   python3 main.py
#   Terminal 2 — open dashboard:    streamlit run streamlit_dashboard.py
#
# Opens at http://localhost:8501  (auto-refreshes every 4 seconds)

import json
import os
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

TRACE_FILE      = os.path.join(os.path.dirname(__file__), "traces.jsonl")
DIVERGENCE_FILE = os.path.join(os.path.dirname(__file__), "divergence_log.jsonl")
REFRESH_INTERVAL = 4


def _load_jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows   # seconds

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="UnderCurrent — Stateful",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    .metric-label  { font-size: 0.85rem; color: #888; }
    .metric-value  { font-size: 2rem; font-weight: 700; }
    div[data-testid="stMetricValue"] { font-size: 2rem; }
    .section-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 0.3rem; }
    [data-testid="stToolbarActions"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ── FSM state colours (matched to Rich dashboard palette) ─────────────────────
FSM_COLORS = {
    "Healthy":   "#4CAF50",
    "Degraded":  "#FFC107",
    "Audited":   "#00BCD4",
    "Repairing": "#CE93D8",
    "Recovered": "#64B5F6",
}

ACTION_COLORS = {
    "no_action":              "#4CAF50",
    "flush_io_queue":         "#00BCD4",
    "checkpoint_and_restart": "#FFC107",
    "escalate":               "#F44336",
}


# ── Data loader ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=REFRESH_INTERVAL)
def load_traces() -> pd.DataFrame:
    if not os.path.exists(TRACE_FILE):
        return pd.DataFrame()
    rows = []
    with open(TRACE_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["trace_time"] = pd.to_datetime(df["trace_time"], unit="s")
    df["score"]      = df["score"].astype(float)
    # Fill optional stateful columns gracefully
    for col in ["fsm_state", "fsm_transition", "reversibility",
                "dag_pattern", "blocked_reason", "node_type"]:
        if col not in df.columns:
            df[col] = None
    if "kernel_signals" not in df.columns:
        df["kernel_signals"] = [[] for _ in range(len(df))]
    return df


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("## UnderCurrent — Stateful Control Plane (Type F)")
st.caption("FSM-gated I/O risk monitoring · auto-refreshes every 4 seconds")
st.divider()

df = load_traces()

# ── Empty state ───────────────────────────────────────────────────────────────
if df.empty:
    st.info(
        "No stateful traces found yet. Start the pipeline first:\n\n"
        "```bash\npython3 main.py\n```\n\n"
        f"Traces will appear here once written to `{TRACE_FILE}`",
        icon="ℹ️",
    )
    time.sleep(REFRESH_INTERVAL)
    st.rerun()

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    all_containers = sorted(df["container"].dropna().unique().tolist())
    sel_containers = st.multiselect("Containers", all_containers, default=all_containers)

    all_actions = sorted(df["action"].dropna().unique().tolist())
    sel_actions = st.multiselect("Actions", all_actions, default=all_actions)

    all_modes = sorted(df["mode"].dropna().unique().tolist())
    sel_modes = st.multiselect(
        "Mode",
        all_modes,
        default=all_modes,
        help=(
            "Filters which trace rows are displayed — NOT a pipeline switch.\n\n"
            "• **real** — decisions that were actually executed\n"
            "• **shadow** — dry-run decisions (never executed)\n\n"
            "To change pipeline mode restart main.py with `--shadow` or `--real`."
        ),
    )

    all_fsm = sorted(df["fsm_state"].dropna().unique().tolist())
    sel_fsm = st.multiselect("FSM State", all_fsm, default=all_fsm) if all_fsm else []

    st.divider()
    st.caption(f"Trace file: `{TRACE_FILE}`")
    st.caption(f"Total rows: {len(df)}")

df_f = df[
    df["container"].isin(sel_containers) &
    df["action"].isin(sel_actions) &
    df["mode"].isin(sel_modes)
]
if sel_fsm:
    df_f = df_f[df_f["fsm_state"].isin(sel_fsm)]

real_df = df_f[df_f["mode"] == "real"]

# ── KPI row ───────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

c1.metric("Total Decisions",   len(df_f))
c2.metric("Flushes",           int((real_df["action"] == "flush_io_queue").sum()))
c3.metric("Chkpt+Restarts",    int((real_df["action"] == "checkpoint_and_restart").sum()))
c4.metric("Escalations",       int((real_df["action"] == "escalate").sum()))
c5.metric("No-Action",         int((real_df["action"] == "no_action").sum()))

# FSM/WASM gate blocks (distinct from shadow divergence)
fsm_blocked = int(df_f["blocked_reason"].notna().sum()) if "blocked_reason" in df_f.columns else 0
c6.metric(
    "FSM/WASM Blocked",
    fsm_blocked,
    help="Actions the FSM gate or WASM sandbox downgraded/suppressed this cycle.",
)

# Shadow divergence rate from divergence_log.jsonl
_div_entries  = _load_jsonl(DIVERGENCE_FILE)
_real_count   = len(real_df)
_div_rate     = f"{len(_div_entries) / _real_count:.1%}" if _real_count else "—"
c7.metric(
    "Divergence Rate",
    _div_rate,
    delta=f"{len(_div_entries)} events",
    delta_color="off",
    help=(
        "Fraction of real decisions where the shadow controller chose a different action.\n\n"
        "Shadow controller runs every cycle with a fresh FSM. Over time its FSM state "
        "drifts from the real FSM → different actions → divergence."
    ),
)

st.divider()

# ── Row 1: Score timeline + Action distribution ───────────────────────────────
col_left, col_right = st.columns([3, 1])

with col_left:
    st.markdown('<div class="section-title">Confidence Score Over Time (real decisions)</div>',
                unsafe_allow_html=True)
    timeline = real_df[real_df["action"] != "no_action"] if not real_df.empty else real_df
    if not timeline.empty:
        fig = px.line(
            timeline.sort_values("trace_time"),
            x="trace_time", y="score", color="container",
            markers=True,
            labels={"trace_time": "Time", "score": "Risk Score", "container": "Container"},
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.add_hline(y=0.95, line_dash="dash", line_color="red",
                      annotation_text="escalate threshold")
        fig.add_hline(y=0.80, line_dash="dash", line_color="orange",
                      annotation_text="repair threshold")
        fig.add_hline(y=0.50, line_dash="dash", line_color="#00BCD4",
                      annotation_text="flush threshold")
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(range=[0, 1.05], gridcolor="#333"),
            xaxis=dict(gridcolor="#333"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=0, r=0, t=30, b=0),
            height=300,
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.caption("No real-path score data yet.")

with col_right:
    st.markdown('<div class="section-title">Action Distribution</div>',
                unsafe_allow_html=True)
    action_counts = df_f["action"].value_counts().reset_index()
    action_counts.columns = ["action", "count"]
    fig2 = px.pie(
        action_counts, names="action", values="count",
        color="action", color_discrete_map=ACTION_COLORS,
        hole=0.55,
    )
    fig2.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
        margin=dict(l=0, r=0, t=10, b=0),
        height=300,
    )
    fig2.update_traces(textposition="outside", textinfo="percent+label")
    st.plotly_chart(fig2, width="stretch")

st.divider()

# ── Row 2: FSM State Timeline ─────────────────────────────────────────────────
st.markdown('<div class="section-title">FSM State Over Time</div>', unsafe_allow_html=True)

fsm_df = real_df[real_df["fsm_state"].notna()].copy()
if not fsm_df.empty:
    # Map FSM states to numeric for scatter colour
    fsm_order = ["Healthy", "Degraded", "Audited", "Repairing", "Recovered"]
    fsm_df["fsm_num"] = fsm_df["fsm_state"].map(
        {s: i for i, s in enumerate(fsm_order)}
    ).fillna(0)

    fig3 = px.scatter(
        fsm_df.sort_values("trace_time"),
        x="trace_time", y="container",
        color="fsm_state",
        color_discrete_map=FSM_COLORS,
        symbol="fsm_state",
        hover_data=["score", "action", "fsm_transition"],
        labels={"trace_time": "Time", "container": "Container", "fsm_state": "FSM State"},
        height=250,
    )
    fig3.update_traces(marker_size=10)
    fig3.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor="#333"),
        yaxis=dict(gridcolor="#333"),
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig3, width="stretch")
else:
    st.caption("No FSM state data yet.")

st.divider()

# ── Row 3: Per-container risk cards ──────────────────────────────────────────
st.markdown('<div class="section-title">Current Risk per Container</div>',
            unsafe_allow_html=True)

latest = (
    real_df.sort_values("trace_time")
           .groupby("container")
           .last()
           .reset_index()[["container", "score", "action", "fsm_state"]]
)

if not latest.empty:
    latest = latest.sort_values("score", ascending=False)
    n    = len(latest)
    cols = st.columns(min(n, 5))
    for i, row in latest.iterrows():
        col   = cols[i % min(n, 5)]
        score = row["score"]
        fsm_s = row["fsm_state"] if pd.notna(row["fsm_state"]) else "?"

        if score >= 0.95:
            label = "CRITICAL"
        elif score >= 0.80:
            label = "WARNING"
        elif score >= 0.50:
            label = "DEGRADED"
        else:
            label = "OK"

        col.metric(
            label=f"{row['container']}",
            value=f"{score:.2f}",
            delta=f"{label} · FSM: {fsm_s}",
            delta_color="off",
        )

st.divider()

# ── Row 4: FSM Transition Log ─────────────────────────────────────────────────
st.markdown('<div class="section-title">FSM Transition Log</div>', unsafe_allow_html=True)

trans_df = df_f[df_f["fsm_transition"].notna()][
    ["trace_time", "container", "fsm_state", "fsm_transition",
     "score", "action", "dag_pattern"]
].sort_values("trace_time", ascending=False).copy()

if not trans_df.empty:
    trans_df["trace_time"] = trans_df["trace_time"].dt.strftime("%H:%M:%S")
    trans_df.columns       = ["Time", "Container", "FSM Before",
                               "Transition", "Score", "Action", "DAG Pattern"]
    st.dataframe(trans_df, width="stretch", height=200)
else:
    st.caption("No FSM transitions recorded yet.")

st.divider()

# ── Row 5: Full audit log ─────────────────────────────────────────────────────
st.markdown('<div class="section-title">Decision Audit Log</div>', unsafe_allow_html=True)

display_cols = ["trace_time", "container", "score", "action", "mode",
                "fsm_state", "reversibility", "why", "executed"]
display_cols = [c for c in display_cols if c in df_f.columns]
display_df   = df_f[display_cols].sort_values("trace_time", ascending=False).copy()
display_df["trace_time"] = display_df["trace_time"].dt.strftime("%H:%M:%S")

# Rename for display
rename = {
    "trace_time": "Time", "container": "Container", "score": "Score",
    "action": "Action", "mode": "Mode", "fsm_state": "FSM State",
    "reversibility": "Reversibility", "why": "Why", "executed": "Executed",
}
display_df = display_df.rename(columns={k: v for k, v in rename.items() if k in display_df.columns})

# Plain dataframe — no jinja2 / .style dependency
st.dataframe(display_df, width="stretch", height=350)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
time.sleep(REFRESH_INTERVAL)
st.rerun()
