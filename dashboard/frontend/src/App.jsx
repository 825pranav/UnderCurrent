import React, { useState, useMemo } from "react";
import Sidebar from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import KPIStrip from "./components/KPIStrip.jsx";
import ScoreTimeline from "./components/ScoreTimeline.jsx";
import ActionDistribution from "./components/ActionDistribution.jsx";
import FSMPanel from "./components/FSMPanel.jsx";
import AuditLog from "./components/AuditLog.jsx";
import { useTraces } from "./hooks/useTraces.js";
import { exportCSV } from "./utils/export.js";

/* ── Process filter bar ──────────────────────────────────────────── */
function ProcessFilter({ allNames, selected, setSelected, modeFilter, setModeFilter }) {
  const modes = [
    { id: "all",    label: "All Modes" },
    { id: "real",   label: "Real",   color: "#4ae183" },
    { id: "shadow", label: "Shadow", color: "#c3c6d1" },
  ];

  const toggle = (name) =>
    setSelected((prev) => prev.includes(name) ? prev.filter((c) => c !== name) : [...prev, name]);

  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* Process pills */}
      <div className="bg-surface-container-low p-1.5 rounded-xl flex flex-wrap gap-1">
        <button
          onClick={() => setSelected([])}
          className={`px-5 py-2 rounded-lg text-sm font-label font-bold transition-all duration-150 ${
            selected.length === 0
              ? "bg-surface-container-highest text-primary"
              : "text-on-surface-variant hover:bg-surface-container/50"
          }`}
        >
          All Processes
        </button>
        {allNames.map((name) => (
          <button
            key={name}
            onClick={() => toggle(name)}
            className={`px-5 py-2 rounded-lg text-sm font-label transition-all duration-150 ${
              selected.includes(name)
                ? "bg-surface-container-highest text-primary font-bold"
                : "text-on-surface-variant hover:bg-surface-container/50"
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      {/* Mode pills */}
      <div className="bg-surface-container-low p-1.5 rounded-xl flex gap-1">
        {modes.map((m) => (
          <button
            key={m.id}
            onClick={() => setModeFilter(m.id)}
            className={`px-4 py-2 rounded-lg text-xs font-label font-semibold transition-all duration-150 ${
              modeFilter === m.id
                ? "bg-surface-container-highest"
                : "text-on-surface-variant hover:bg-surface-container/50"
            }`}
            style={modeFilter === m.id && m.color ? { color: m.color } : {}}
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ── Page header ──────────────────────────────────────────────────── */
function PageHeader({ activeTab, stats, traces }) {
  const TAB_META = {
    overview:  { title: "System Overview",  icon: "dashboard",      badge: "Live Engine" },
    stateless: { title: "Stateless Track",  icon: "dataset",        badge: "Type S" },
    stateful:  { title: "Stateful Track",   icon: "database",       badge: "Type F · FSM" },
    shadow:    { title: "Shadow Track",     icon: "visibility_off", badge: "Dry-Run" },
    audit:     { title: "Audit Log",        icon: "history",        badge: "Append-Only" },
    settings:  { title: "Settings",         icon: "settings",       badge: null },
  };

  const meta = TAB_META[activeTab] ?? { title: activeTab, icon: "circle", badge: null };
  const realTraces = traces.filter((t) => t.mode === "real");
  const peakScore  = realTraces.length ? Math.max(...realTraces.map((t) => t.score ?? 0)) : 0;

  return (
    <div className="flex justify-between items-end mb-8">
      <div className="space-y-2">
        <nav className="flex items-center gap-1.5 text-xs font-label text-on-surface-variant/60">
          <span>Dashboard</span>
          <span className="material-symbols-outlined text-[10px]">chevron_right</span>
          <span className="text-primary/80">{meta.title}</span>
        </nav>
        <h2 className="text-4xl font-extrabold font-headline tracking-tight text-on-surface flex items-center gap-4">
          {meta.title}
          {meta.badge && (
            <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold border"
                  style={{ background: "rgba(0,218,243,0.08)", color: "#00daf3", borderColor: "rgba(0,218,243,0.2)" }}>
              {meta.badge}
            </span>
          )}
        </h2>
      </div>

      {stats && activeTab !== "settings" && (
        <div className="flex gap-8">
          <div className="text-right">
            <p className="text-[10px] uppercase tracking-widest text-on-surface-variant font-label">Total Decisions</p>
            <p className="text-3xl font-headline font-extrabold text-on-surface">
              {stats.total?.toLocaleString() ?? "—"}
              <span className="text-xs font-normal text-tertiary ml-2">live</span>
            </p>
          </div>
          <div className="text-right">
            <p className="text-[10px] uppercase tracking-widest text-on-surface-variant font-label">Peak Risk Score</p>
            <p className="text-3xl font-headline font-extrabold"
               style={{ color: peakScore >= 0.8 ? "#ffb4ab" : peakScore >= 0.5 ? "#f4a52a" : "#4ae183" }}>
              {peakScore.toFixed(2)}
              <span className={`text-xs font-normal ml-2 ${peakScore >= 0.8 ? "text-error" : "text-tertiary"}`}>
                {peakScore >= 0.95 ? "critical" : peakScore >= 0.8 ? "warning" : "normal"}
              </span>
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Alert banner ─────────────────────────────────────────────────── */
function AlertBanner({ traces }) {
  const critical = useMemo(() => {
    const recent = traces.filter((t) => t.mode === "real" && (t.score ?? 0) >= 0.95);
    const byContainer = {};
    recent.forEach((t) => { byContainer[t.container] = t; });
    return Object.values(byContainer);
  }, [traces]);

  if (critical.length === 0) return null;

  return (
    <div className="flex items-start gap-3 px-5 py-4 rounded-2xl border mb-6"
         style={{ background: "rgba(255,180,171,0.06)", borderColor: "rgba(255,180,171,0.25)" }}>
      <span className="material-symbols-outlined text-error mt-0.5 flex-shrink-0"
            style={{ fontVariationSettings: "'FILL' 1" }}>warning</span>
      <div>
        <p className="text-sm font-bold text-error font-headline">
          {critical.length} container{critical.length > 1 ? "s" : ""} at critical risk
        </p>
        <p className="text-xs text-on-surface-variant mt-0.5">
          {critical.map((c) => `${c.container} (${(c.score ?? 0).toFixed(2)})`).join(" · ")}
        </p>
      </div>
    </div>
  );
}

/* ── Overview tab ─────────────────────────────────────────────────── */
function OverviewTab({ stats, traces, timeline, containers, isLoading, searchQuery, setActiveTab }) {
  return (
    <div className="space-y-8">
      <AlertBanner traces={traces} />
      <KPIStrip stats={stats} timeline={timeline} />
      <div className="grid grid-cols-12 gap-8">
        <div className="col-span-12 xl:col-span-8">
          <ScoreTimeline timeline={timeline} />
        </div>
        <div className="col-span-12 xl:col-span-4">
          <ActionDistribution traces={traces} />
        </div>
      </div>
      <FSMPanel containers={containers} onViewAll={() => setActiveTab("stateful")} />
      <AuditLog traces={traces} searchQuery={searchQuery} isLoading={isLoading} />
    </div>
  );
}

/* ── Track tab ────────────────────────────────────────────────────── */
function TrackTab({ traces, timeline, containers, isLoading, searchQuery, track, setActiveTab, stats }) {
  const filteredTraces     = useMemo(() => traces.filter((t) => t.track === track), [traces, track]);
  const filteredContainers = useMemo(() => containers.filter((c) => c.track === track), [containers, track]);
  const filteredTimeline   = useMemo(() => {
    const cSet = new Set(filteredContainers.map((c) => c.container));
    return Object.fromEntries(Object.entries(timeline).filter(([k]) => cSet.has(k)));
  }, [timeline, filteredContainers]);

  return (
    <div className="space-y-8">
      <AlertBanner traces={filteredTraces} />
      <div className="grid grid-cols-12 gap-8">
        <div className="col-span-12 xl:col-span-8">
          <ScoreTimeline timeline={filteredTimeline} />
        </div>
        <div className="col-span-12 xl:col-span-4">
          <ActionDistribution traces={filteredTraces} />
        </div>
      </div>
      <FSMPanel
        containers={filteredContainers}
        onViewAll={track === "S" ? undefined : () => setActiveTab("stateful")}
      />
      <AuditLog traces={filteredTraces} searchQuery={searchQuery} isLoading={isLoading} />
    </div>
  );
}

/* ── Shadow tab ───────────────────────────────────────────────────── */
function ShadowTab({ traces, isLoading, searchQuery }) {
  const shadowTraces = useMemo(() => traces.filter((t) => t.mode === "shadow"), [traces]);
  const realTraces   = useMemo(() => traces.filter((t) => t.mode === "real"),   [traces]);

  const shadowTimeline = useMemo(() => {
    const byContainer = {};
    shadowTraces.forEach((t) => {
      const c = t.container ?? "unknown";
      if (!byContainer[c]) byContainer[c] = [];
      byContainer[c].push({ time: t.trace_time ?? 0, score: t.score ?? 0 });
    });
    return byContainer;
  }, [shadowTraces]);

  const divergences = useMemo(() => {
    const realMap = new Map();
    realTraces.forEach((r) => {
      realMap.set(`${r.container}:${Math.round(r.trace_time ?? 0)}`, r);
    });
    return shadowTraces.filter((s) => {
      const real = realMap.get(`${s.container}:${Math.round(s.trace_time ?? 0)}`);
      return real && real.action !== s.action;
    });
  }, [shadowTraces, realTraces]);

  const kpis = [
    { label: "Shadow Decisions", value: shadowTraces.length, color: "#c3c6d1", icon: "visibility_off" },
    { label: "Real Decisions",   value: realTraces.length,   color: "#4ae183", icon: "check_circle"  },
    { label: "Divergences",      value: divergences.length,  color: "#ffb4ab", icon: "warning"       },
    {
      label: "Divergence Rate",
      value: shadowTraces.length > 0
        ? `${((divergences.length / shadowTraces.length) * 100).toFixed(1)}%`
        : "—",
      color: "#f4a52a",
      icon: "percent",
    },
  ];

  return (
    <div className="space-y-8">
      {/* Info banner */}
      <div className="flex items-start gap-3 px-5 py-4 rounded-2xl border"
           style={{ background: "rgba(195,198,209,0.06)", borderColor: "rgba(195,198,209,0.2)" }}>
        <span className="material-symbols-outlined text-secondary mt-0.5 flex-shrink-0">visibility_off</span>
        <div>
          <p className="text-sm font-bold text-secondary font-headline">Shadow Mode — Dry-Run Parallel Path</p>
          <p className="text-xs text-on-surface-variant mt-0.5">
            Decisions are computed but never executed. Divergences show where the shadow path would choose
            differently from the real controller under identical conditions.
          </p>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
        {kpis.map((k) => (
          <div key={k.label} className="bg-surface-container rounded-2xl p-6 border border-outline-variant/20 flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-label font-medium text-on-surface-variant uppercase tracking-widest">{k.label}</span>
              <span className="material-symbols-outlined text-xl" style={{ color: k.color }}>{k.icon}</span>
            </div>
            <span className="font-headline text-3xl font-extrabold" style={{ color: k.color }}>{k.value}</span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-12 gap-8">
        <div className="col-span-12 xl:col-span-8">
          <ScoreTimeline timeline={shadowTimeline} />
        </div>
        <div className="col-span-12 xl:col-span-4">
          <ActionDistribution traces={shadowTraces} />
        </div>
      </div>

      {/* Divergence table */}
      {divergences.length > 0 && (
        <div className="bg-surface-container rounded-2xl border border-outline-variant/20 overflow-hidden">
          <div className="flex items-center justify-between px-8 py-5 border-b border-outline-variant/20">
            <div>
              <h3 className="font-headline font-bold text-lg text-on-surface">Divergences</h3>
              <p className="text-xs text-on-surface-variant mt-0.5">Where shadow ≠ real</p>
            </div>
            <span className="text-xs font-bold px-3 py-1 rounded-full"
                  style={{ background: "rgba(255,180,171,0.1)", color: "#ffb4ab" }}>
              {divergences.length} found
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-outline-variant/20 bg-surface-container-low/60">
                  {["Container", "Track", "Shadow Action", "Real Action", "Score"].map((h) => (
                    <th key={h} className="px-6 py-4 text-[10px] font-label font-medium text-on-surface-variant uppercase tracking-widest whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-outline-variant/10">
                {divergences.slice(0, 25).map((s, i) => {
                  const real = realTraces.find(
                    (r) => r.container === s.container && Math.abs((r.trace_time ?? 0) - (s.trace_time ?? 0)) < 2
                  );
                  return (
                    <tr key={i} className="hover:bg-surface-container-high transition-colors border-l-2 border-l-error/60">
                      <td className="px-6 py-4 text-sm font-bold text-primary">{s.container}</td>
                      <td className="px-6 py-4">
                        <span className="inline-flex items-center justify-center w-7 h-6 text-[10px] font-bold rounded"
                              style={{ background: s.track === "S" ? "rgba(0,218,243,0.12)" : "rgba(195,198,209,0.12)",
                                       color: s.track === "S" ? "#00daf3" : "#c3c6d1" }}>
                          {s.track}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="px-2 py-0.5 rounded text-xs font-bold"
                              style={{ background: "rgba(195,198,209,0.1)", color: "#c3c6d1" }}>
                          {(s.action ?? "").replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="px-2 py-0.5 rounded text-xs font-bold"
                              style={{ background: "rgba(74,225,131,0.1)", color: "#4ae183" }}>
                          {(real?.action ?? "—").replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-sm font-bold tabular-nums" style={{ color: "#f4a52a" }}>
                        {(s.score ?? 0).toFixed(3)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <AuditLog traces={shadowTraces} searchQuery={searchQuery} isLoading={isLoading} />
    </div>
  );
}

/* ── Settings tab ─────────────────────────────────────────────────── */
function SettingsTab() {
  const rows = [
    { label: "Refresh Interval",  sub: "How often the dashboard polls the backend",      value: "4 seconds"                },
    { label: "Backend URL",       sub: "Flask API endpoint",                              value: "localhost:5050", mono: true },
    { label: "Trace Sources",     sub: "Files being watched by the backend",              value: "stateless/traces.jsonl\nstateful/traces.jsonl", mono: true, multi: true },
    { label: "Real-Mode Default", sub: "run.py starts both tracks in real mode",          value: "python3 run.py", mono: true },
    { label: "Shadow Command",    sub: "shadow.py starts both tracks in dry-run mode",    value: "python3 shadow.py", mono: true },
    { label: "Streamlit Alt",     sub: "Separate Streamlit dashboard if preferred",       value: "python3 streamlit_dash.py", mono: true },
  ];

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h2 className="font-headline font-extrabold text-2xl text-on-surface">Settings</h2>
        <p className="text-sm text-on-surface-variant mt-1">Dashboard configuration and quick reference.</p>
      </div>
      <div className="bg-surface-container rounded-2xl border border-outline-variant/20 overflow-hidden">
        {rows.map((row, i) => (
          <div key={row.label}
               className={`flex items-center justify-between px-8 py-5 ${i < rows.length - 1 ? "border-b border-outline-variant/15" : ""}`}>
            <div>
              <p className="text-sm font-bold text-on-surface font-headline">{row.label}</p>
              <p className="text-xs text-on-surface-variant mt-0.5">{row.sub}</p>
            </div>
            {row.multi ? (
              <div className="text-right text-xs font-mono text-on-surface-variant">
                {row.value.split("\n").map((v) => <p key={v}>{v}</p>)}
              </div>
            ) : (
              <span className={`text-sm ${row.mono ? "font-mono text-on-surface-variant" : "font-bold text-primary"}`}>
                {row.value}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── App ──────────────────────────────────────────────────────────── */
export default function App() {
  const [darkMode, setDarkMode]                     = useState(true);
  const [searchQuery, setSearchQuery]               = useState("");
  const [activeTab, setActiveTab]                   = useState("overview");
  const [modeFilter, setModeFilter]                 = useState("all");
  const [selectedContainers, setSelectedContainers] = useState([]);

  const { traces, stats, containers, timeline, isLoading, isError } = useTraces();

  React.useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
  }, [darkMode]);

  const allContainerNames = useMemo(
    () => [...new Set(traces.map((t) => t.container).filter(Boolean))].sort(),
    [traces]
  );

  const filteredTraces = useMemo(() => {
    let r = traces;
    if (modeFilter !== "all")           r = r.filter((t) => t.mode === modeFilter);
    if (selectedContainers.length > 0)  r = r.filter((t) => selectedContainers.includes(t.container));
    return r;
  }, [traces, modeFilter, selectedContainers]);

  const filteredContainers = useMemo(() =>
    selectedContainers.length === 0 ? containers : containers.filter((c) => selectedContainers.includes(c.container)),
    [containers, selectedContainers]
  );

  const filteredTimeline = useMemo(() => {
    if (selectedContainers.length === 0) return timeline;
    return Object.fromEntries(Object.entries(timeline).filter(([k]) => selectedContainers.includes(k)));
  }, [timeline, selectedContainers]);

  const renderContent = () => {
    const commonProps = {
      traces: filteredTraces, timeline: filteredTimeline,
      containers: filteredContainers, isLoading, searchQuery, setActiveTab, stats,
    };
    switch (activeTab) {
      case "overview":  return <OverviewTab {...commonProps} />;
      case "stateless": return <TrackTab {...commonProps} track="S" />;
      case "stateful":  return <TrackTab {...commonProps} track="F" />;
      case "shadow": {
        const shadowTabTraces = selectedContainers.length > 0
          ? traces.filter((t) => selectedContainers.includes(t.container))
          : traces;
        return <ShadowTab traces={shadowTabTraces} isLoading={isLoading} searchQuery={searchQuery} />;
      }
      case "audit":
        return (
          <div className="space-y-4">
            <AuditLog traces={filteredTraces} searchQuery={searchQuery} isLoading={isLoading} />
          </div>
        );
      case "settings": return <SettingsTab />;
      default: return null;
    }
  };

  return (
    <div className="min-h-screen bg-background text-on-surface">
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} darkMode={darkMode} setDarkMode={setDarkMode} />

      <div className="ml-64 flex flex-col min-h-screen">
        <TopBar
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          activeTab={activeTab}
          traces={filteredTraces}
          stats={stats}
        />

        <main className="flex-1 mt-20 px-10 py-10 space-y-6 overflow-auto">
          {/* Error banner */}
          {isError && (
            <div className="flex items-center gap-3 px-5 py-4 rounded-2xl border"
                 style={{ background: "rgba(255,180,171,0.06)", borderColor: "rgba(255,180,171,0.25)" }}>
              <span className="material-symbols-outlined text-error">error</span>
              <p className="text-sm text-error font-body">
                Cannot reach backend at <span className="font-mono">localhost:5050</span>.
                Run: <span className="font-mono font-bold">python3 run.py</span>
              </p>
            </div>
          )}

          {activeTab !== "settings" && (
            <>
              <PageHeader activeTab={activeTab} stats={stats} traces={filteredTraces} />
              <ProcessFilter
                allNames={allContainerNames}
                selected={selectedContainers}
                setSelected={setSelectedContainers}
                modeFilter={modeFilter}
                setModeFilter={setModeFilter}
              />
            </>
          )}

          {renderContent()}
        </main>

        <footer className="px-10 py-4 border-t border-outline-variant/20 text-xs text-on-surface-variant flex items-center justify-between no-print">
          <span className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-tertiary animate-pulse" />
            UnderCurrent · Adaptive Control Plane
          </span>
          <span>Type-S + Type-F · Flask 5050</span>
        </footer>
      </div>
    </div>
  );
}
