# integration/unified_dashboard.py — Unified Web Dashboard
# Reads BOTH stateless/traces.jsonl and stateful/traces.jsonl and presents
# a single view of the entire UnderCurrent control plane.
#
# Install:  pip install streamlit pandas plotly
# Run from repo root:
#   streamlit run integration/unified_dashboard.py
# Opens at http://localhost:8501

import json
import os
import time

import pandas as pd
import plotly.express as px
import streamlit as st

REPO_ROOT       = os.path.join(os.path.dirname(__file__), "..")
STATELESS_TRACE = os.path.join(REPO_ROOT, "stateless", "traces.jsonl")
STATEFUL_TRACE  = os.path.join(REPO_ROOT, "stateful",  "traces.jsonl")
REFRESH_INTERVAL = 4

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="UnderCurrent — Unified",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .stMetric > div { background: #1e1e2e; border-radius: 8px; padding: 12px 16px; }
    .track-s { color: #60a5fa; font-weight: 700; }
    .track-f { color: #a78bfa; font-weight: 700; }
    .section-title { font-size: 1.05rem; font-weight: 600; color: #cdd6f4; margin-bottom: 0.3rem; }
</style>
""", unsafe_allow_html=True)


# ── Data loader ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=REFRESH_INTERVAL)
def load_traces() -> pd.DataFrame:
    frames = []

    for path, track, node_type in [
        (STATELESS_TRACE, "Stateless (S)", "S"),
        (STATEFUL_TRACE,  "Stateful (F)",  "F"),
    ]:
        if not os.path.exists(path):
            continue
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        if rows:
            df = pd.DataFrame(rows)
            df["track"]     = track
            df["node_type"] = node_type
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["trace_time"] = pd.to_datetime(combined["trace_time"], unit="s")
    combined["score"]      = combined["score"].astype(float)
    return combined


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("## ⚡ UnderCurrent — Unified Control Plane Dashboard")
st.caption("Stateless (Type S) + Stateful (Type F) · auto-refreshes every 4 seconds")

col_s, col_f = st.columns(2)
col_s.markdown('<span class="track-s">■ Stateless (S)</span> — restart · reschedule', unsafe_allow_html=True)
col_f.markdown('<span class="track-f">■ Stateful (F)</span> — flush · checkpoint_restart · escalate', unsafe_allow_html=True)
st.divider()

df = load_traces()

# ── Empty state ───────────────────────────────────────────────────────────────
if df.empty:
    st.info(
        "No traces found. Start both pipelines first:\n\n"
        "```bash\n# Terminal 1\npython3 stateless/main.py\n\n"
        "# Terminal 2\npython3 stateful/main.py\n```",
        icon="ℹ️",
    )
    time.sleep(REFRESH_INTERVAL)
    st.rerun()

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    sel_tracks     = st.multiselect("Track", df["track"].unique().tolist(),
                                    default=df["track"].unique().tolist())
    sel_containers = st.multiselect("Containers", sorted(df["container"].unique()),
                                    default=sorted(df["container"].unique()))
    sel_modes      = st.multiselect("Mode", df["mode"].unique().tolist(),
                                    default=df["mode"].unique().tolist())
    st.divider()
    st.caption(f"Total trace entries: {len(df)}")

df_f = df[
    df["track"].isin(sel_tracks) &
    df["container"].isin(sel_containers) &
    df["mode"].isin(sel_modes)
]
real_df = df_f[df_f["mode"] == "real"]

# ── KPI row — split by track ──────────────────────────────────────────────────
s_real = real_df[real_df["node_type"] == "S"]
f_real = real_df[real_df["node_type"] == "F"]

st.markdown("#### Stateless (S) — Type S track")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Decisions",   len(s_real))
c2.metric("Restarts",    int((s_real["action"] == "restart").sum()))
c3.metric("Reschedules", int((s_real["action"] == "reschedule").sum()))
c4.metric("No-action",   int((s_real["action"] == "no_action").sum()))
c5.metric("Peak Score",  f"{s_real['score'].max():.2f}" if not s_real.empty else "—")

st.markdown("#### Stateful (F) — Type F track")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Decisions",        len(f_real))
c2.metric("Flush IO",         int((f_real["action"] == "flush_io_queue").sum()))
c3.metric("Checkpoint Restart", int((f_real["action"] == "checkpoint_and_restart").sum()))
c4.metric("Escalations",      int((f_real["action"] == "escalate").sum()))
c5.metric("Peak Score",       f"{f_real['score'].max():.2f}" if not f_real.empty else "—")

st.divider()

# ── Row 1: Score timeline (both tracks) ───────────────────────────────────────
st.markdown('<div class="section-title">Confidence Score Over Time — Both Tracks</div>',
            unsafe_allow_html=True)

timeline = real_df.sort_values("trace_time")
if not timeline.empty:
    fig = px.line(
        timeline, x="trace_time", y="score",
        color="container", line_dash="track",
        markers=True,
        labels={"trace_time": "Time", "score": "Risk Score",
                "container": "Container", "track": "Track"},
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.add_hline(y=0.95, line_dash="dash", line_color="red",
                  annotation_text="reschedule / escalate threshold")
    fig.add_hline(y=0.80, line_dash="dash", line_color="orange",
                  annotation_text="restart / repair threshold")
    fig.add_hline(y=0.50, line_dash="dot",  line_color="#888",
                  annotation_text="flush threshold (F only)")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(range=[0, 1.05], gridcolor="#333"),
        xaxis=dict(gridcolor="#333"),
        margin=dict(l=0, r=0, t=30, b=0), height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── Row 2: Action distribution (side by side) ─────────────────────────────────
col_s, col_f = st.columns(2)

with col_s:
    st.markdown('<div class="section-title">Action Distribution — Stateless (S)</div>',
                unsafe_allow_html=True)
    s_counts = s_real["action"].value_counts().reset_index()
    s_counts.columns = ["action", "count"]
    color_map_s = {"no_action": "#4CAF50", "restart": "#FFC107", "reschedule": "#F44336"}
    if not s_counts.empty:
        fig_s = px.pie(s_counts, names="action", values="count",
                       color="action", color_discrete_map=color_map_s, hole=0.5)
        fig_s.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                             margin=dict(l=0, r=0, t=10, b=0), height=260)
        st.plotly_chart(fig_s, use_container_width=True)
    else:
        st.caption("No stateless traces yet.")

with col_f:
    st.markdown('<div class="section-title">Action Distribution — Stateful (F)</div>',
                unsafe_allow_html=True)
    f_counts = f_real["action"].value_counts().reset_index()
    f_counts.columns = ["action", "count"]
    color_map_f = {
        "no_action":             "#4CAF50",
        "flush_io_queue":        "#60a5fa",
        "checkpoint_and_restart":"#FFC107",
        "escalate":              "#F44336",
    }
    if not f_counts.empty:
        fig_f = px.pie(f_counts, names="action", values="count",
                       color="action", color_discrete_map=color_map_f, hole=0.5)
        fig_f.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                             margin=dict(l=0, r=0, t=10, b=0), height=260)
        st.plotly_chart(fig_f, use_container_width=True)
    else:
        st.caption("No stateful traces yet.")

st.divider()

# ── Row 3: FSM state panel (stateful only) ────────────────────────────────────
if "fsm_state" in df_f.columns and not f_real.empty:
    st.markdown('<div class="section-title">FSM State — Current per Container (Stateful)</div>',
                unsafe_allow_html=True)

    fsm_latest = (
        f_real.dropna(subset=["fsm_state"])
              .sort_values("trace_time")
              .groupby("container")
              .last()
              .reset_index()[["container", "fsm_state", "score", "action"]]
    )

    state_colors = {
        "Healthy":   "🟢", "Recovered": "🔵",
        "Degraded":  "🟡", "Audited":   "🟠",
        "Repairing": "🔴",
    }

    cols = st.columns(min(len(fsm_latest), 5))
    for i, row in fsm_latest.iterrows():
        icon = state_colors.get(row["fsm_state"], "⚪")
        cols[i % 5].metric(
            label=f"{icon} {row['container']}",
            value=row["fsm_state"],
            delta=f"score {row['score']:.2f} · {row['action']}",
            delta_color="off",
        )
    st.divider()

# ── Row 4: Unified audit log ──────────────────────────────────────────────────
st.markdown('<div class="section-title">Unified Decision Audit Log</div>',
            unsafe_allow_html=True)

base_cols = ["trace_time", "track", "container", "score", "action", "mode", "why"]
extra_cols = [c for c in ["fsm_state", "fsm_transition", "reversibility", "blocked_reason"]
              if c in df_f.columns]
display_cols = base_cols + extra_cols

display_df = df_f[display_cols].sort_values("trace_time", ascending=False).copy()
display_df["trace_time"] = display_df["trace_time"].dt.strftime("%H:%M:%S")

st.dataframe(display_df, use_container_width=True, height=380)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
time.sleep(REFRESH_INTERVAL)
st.rerun()
