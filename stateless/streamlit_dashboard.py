# streamlit_dashboard.py — S10: Web dashboard (reads traces.jsonl live)
#
# Install:  pip install streamlit pandas plotly
# Run:      streamlit run streamlit_dashboard.py
#           (keep main.py or dashboard.py running in another terminal to generate traces)

import json
import os
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

TRACE_FILE = os.path.join(os.path.dirname(__file__), "traces.jsonl")
REFRESH_INTERVAL = 4  # seconds between auto-refresh

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="UnderCurrent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    .metric-label  { font-size: 0.85rem; color: #888; }
    .metric-value  { font-size: 2rem; font-weight: 700; }
    .stMetric > div { background: #1e1e2e; border-radius: 8px; padding: 12px 16px; }
    div[data-testid="stMetricValue"] { font-size: 2rem; }
    .section-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 0.3rem; color: #cdd6f4; }
</style>
""", unsafe_allow_html=True)


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
    df["score"] = df["score"].astype(float)
    return df


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("## ⚡ UnderCurrent — Risk-Aware Autonomic Control Plane")
st.caption("Live decision audit log · auto-refreshes every 4 seconds")
st.divider()

df = load_traces()

# ── Empty state ───────────────────────────────────────────────────────────────
if df.empty:
    st.info(
        "No traces found yet. Start the pipeline first:\n\n"
        "```bash\npython3 main.py\n```\n\n"
        f"Traces will appear here once written to `{TRACE_FILE}`",
        icon="ℹ️",
    )
    time.sleep(REFRESH_INTERVAL)
    st.rerun()

# ── Filter sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    all_containers = sorted(df["container"].unique().tolist())
    selected = st.multiselect("Containers", all_containers, default=all_containers)
    all_actions = sorted(df["action"].unique().tolist())
    sel_actions = st.multiselect("Actions", all_actions, default=all_actions)
    all_modes = sorted(df["mode"].unique().tolist())
    sel_modes = st.multiselect("Mode", all_modes, default=all_modes)
    st.divider()
    st.caption(f"Trace file: `{TRACE_FILE}`")
    st.caption(f"Total rows: {len(df)}")

df_f = df[
    df["container"].isin(selected) &
    df["action"].isin(sel_actions) &
    df["mode"].isin(sel_modes)
]

# ── KPI metrics ───────────────────────────────────────────────────────────────
real_df = df_f[df_f["mode"] == "real"]
c1, c2, c3, c4, c5 = st.columns(5)

c1.metric("Total Decisions",  len(df_f))
c2.metric("Restarts",         int((real_df["action"] == "restart").sum()))
c3.metric("Reschedules",      int((real_df["action"] == "reschedule").sum()))
c4.metric("No-Action",        int((real_df["action"] == "no_action").sum()))

if not real_df.empty:
    peak_score = real_df["score"].max()
    peak_container = real_df.loc[real_df["score"].idxmax(), "container"]
    c5.metric("Peak Risk Score", f"{peak_score:.2f}", delta=peak_container, delta_color="inverse")
else:
    c5.metric("Peak Risk Score", "—")

st.divider()

# ── Row 1: Score timeline + action distribution ───────────────────────────────
col_left, col_right = st.columns([3, 1])

with col_left:
    st.markdown('<div class="section-title">Confidence Score Over Time (real decisions)</div>', unsafe_allow_html=True)
    timeline = real_df[real_df["action"] != "no_action"] if not real_df.empty else real_df
    if not timeline.empty:
        fig = px.line(
            timeline.sort_values("trace_time"),
            x="trace_time", y="score", color="container",
            markers=True,
            labels={"trace_time": "Time", "score": "Risk Score", "container": "Container"},
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.add_hline(y=0.95, line_dash="dash", line_color="red",    annotation_text="reschedule threshold")
        fig.add_hline(y=0.80, line_dash="dash", line_color="orange", annotation_text="restart threshold")
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
    st.markdown('<div class="section-title">Action Distribution</div>', unsafe_allow_html=True)
    action_counts = df_f["action"].value_counts().reset_index()
    action_counts.columns = ["action", "count"]
    color_map = {"no_action": "#4CAF50", "restart": "#FFC107", "reschedule": "#F44336"}
    fig2 = px.pie(
        action_counts, names="action", values="count",
        color="action", color_discrete_map=color_map,
        hole=0.55,
    )
    fig2.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
        margin=dict(l=0, r=0, t=10, b=0),
        height=300,
    )
    fig2.update_traces(textposition="outside", textinfo="percent+label")
    st.plotly_chart(fig2, width="stretch")

st.divider()

# ── Row 2: Current risk per container ─────────────────────────────────────────
st.markdown('<div class="section-title">Current Risk per Container</div>', unsafe_allow_html=True)

latest = (
    real_df.sort_values("trace_time")
           .groupby("container")
           .last()
           .reset_index()[["container", "score", "action"]]
)

if not latest.empty:
    latest = latest.sort_values("score", ascending=False)
    n = len(latest)
    cols = st.columns(min(n, 5))
    for i, row in latest.iterrows():
        col = cols[i % min(n, 5)]
        score = row["score"]
        if score >= 0.95:
            color, label = "🔴", "CRITICAL"
        elif score >= 0.80:
            color, label = "🟡", "WARNING"
        elif score >= 0.40:
            color, label = "🟠", "ELEVATED"
        else:
            color, label = "🟢", "OK"
        col.metric(
            label=f"{color} {row['container']}",
            value=f"{score:.2f}",
            delta=f"{label} · {row['action']}",
            delta_color="off",
        )

st.divider()

# ── Row 3: Trace log table ────────────────────────────────────────────────────
st.markdown('<div class="section-title">Decision Audit Log</div>', unsafe_allow_html=True)

display_cols = ["trace_time", "container", "score", "action", "mode", "why", "executed"]
display_df = df_f[display_cols].sort_values("trace_time", ascending=False).copy()
display_df["trace_time"] = display_df["trace_time"].dt.strftime("%H:%M:%S")
display_df.columns = ["Time", "Container", "Score", "Action", "Mode", "Why", "Executed"]

st.dataframe(
    display_df,
    width="stretch",
    height=350,
)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
time.sleep(REFRESH_INTERVAL)
st.rerun()
