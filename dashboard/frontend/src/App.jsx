import React, { useState, useMemo } from "react";
import Sidebar from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import KPIStrip from "./components/KPIStrip.jsx";
import ScoreTimeline from "./components/ScoreTimeline.jsx";
import ActionDistribution from "./components/ActionDistribution.jsx";
import FSMPanel from "./components/FSMPanel.jsx";
import AuditLog from "./components/AuditLog.jsx";
import { useTraces } from "./hooks/useTraces.js";

/* ── FilterBar ───────────────────────────────────────────────── */

function FilterBar({ modeFilter, setModeFilter, allContainerNames, selectedContainers, setSelectedContainers }) {
  const modes = [
    { id: "all",    label: "All",    color: "#8b919d" },
    { id: "real",   label: "Real",   color: "#4ade80" },
    { id: "shadow", label: "Shadow", color: "#a78bfa" },
  ];

  const toggleContainer = (name) => {
    setSelectedContainers((prev) =>
      prev.includes(name) ? prev.filter((c) => c !== name) : [...prev, name]
    );
  };

  const clearContainers = () => setSelectedContainers([]);

  return (
    <div className="glass-card rounded-lg px-4 py-3 flex flex-wrap items-center gap-4 mb-6">
      {/* Mode pills */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-label font-medium text-outline uppercase tracking-wider">Mode</span>
        <div className="flex items-center gap-1">
          {modes.map((m) => (
            <button
              key={m.id}
              onClick={() => setModeFilter(m.id)}
              className="text-xs font-label font-semibold px-2.5 py-1 rounded transition-all duration-150"
              style={
                modeFilter === m.id
                  ? { background: m.color + "33", color: m.color, border: `1px solid ${m.color}66` }
                  : { background: "transparent", color: "#8b919d", border: "1px solid #3a3a4a" }
              }
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* Divider */}
      <div className="w-px h-5 bg-outline-variant" />

      {/* Container chips */}
      <div className="flex items-center gap-2 flex-wrap flex-1 min-w-0">
        <span className="text-xs font-label font-medium text-outline uppercase tracking-wider flex-shrink-0">
          Process
        </span>
        {allContainerNames.length === 0 ? (
          <span className="text-xs text-outline italic">No containers</span>
        ) : (
          <>
            {allContainerNames.map((name) => {
              const active = selectedContainers.includes(name);
              return (
                <button
                  key={name}
                  onClick={() => toggleContainer(name)}
                  className="text-xs font-label font-medium px-2 py-0.5 rounded transition-all duration-150 flex-shrink-0"
                  style={
                    active
                      ? { background: "#60a5fa33", color: "#60a5fa", border: "1px solid #60a5fa66" }
                      : { background: "transparent", color: "#8b919d", border: "1px solid #3a3a4a" }
                  }
                >
                  {name}
                </button>
              );
            })}
            {selectedContainers.length > 0 && (
              <button
                onClick={clearContainers}
                className="text-xs text-outline hover:text-on-surface flex items-center gap-0.5 transition-colors flex-shrink-0"
              >
                <span className="material-symbols-outlined text-sm">close</span>
                Clear
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ── Tab views ─────────────────────────────────────────────────── */

function OverviewTab({ stats, traces, timeline, containers, isLoading, searchQuery, setActiveTab }) {
  return (
    <div className="space-y-6">
      <KPIStrip stats={stats} timeline={timeline} />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2">
          <ScoreTimeline timeline={timeline} />
        </div>
        <ActionDistribution traces={traces} />
      </div>
      <FSMPanel
        containers={containers}
        onViewAll={() => setActiveTab("stateful")}
      />
      <AuditLog traces={traces} searchQuery={searchQuery} isLoading={isLoading} />
    </div>
  );
}

function TrackTab({ traces, timeline, containers, isLoading, searchQuery, track, setActiveTab }) {
  const filteredTraces     = useMemo(() => traces.filter((t) => t.track === track), [traces, track]);
  const filteredContainers = useMemo(() => containers.filter((c) => c.track === track), [containers, track]);

  const filteredTimeline = useMemo(() => {
    const containerSet = new Set(filteredContainers.map((c) => c.container));
    return Object.fromEntries(
      Object.entries(timeline).filter(([k]) => containerSet.has(k))
    );
  }, [timeline, filteredContainers]);

  const label  = track === "S" ? "Stateless" : "Stateful";
  const accent = track === "S" ? "text-primary-container" : "text-secondary";

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 pb-2 border-b border-outline-variant">
        <span
          className="inline-block px-3 py-1 rounded text-sm font-label font-semibold"
          style={{
            background: track === "S" ? "#1e3a5f" : "#2e1b5e",
            color:      track === "S" ? "#60a5fa" : "#a78bfa",
          }}
        >
          {track} Track
        </span>
        <h2 className={`font-headline font-bold text-xl ${accent}`}>{label} Track</h2>
        <span className="text-sm text-outline ml-auto">
          {filteredTraces.length} decisions · {filteredContainers.length} containers
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2">
          <ScoreTimeline timeline={filteredTimeline} />
        </div>
        <ActionDistribution traces={filteredTraces} />
      </div>
      <FSMPanel
        containers={filteredContainers}
        onViewAll={track === "S" ? undefined : () => setActiveTab("stateful")}
      />
      <AuditLog traces={filteredTraces} searchQuery={searchQuery} isLoading={isLoading} />
    </div>
  );
}

function ShadowTab({ traces, isLoading, searchQuery, setActiveTab }) {
  const shadowTraces = useMemo(() => traces.filter((t) => t.mode === "shadow"), [traces]);
  const realTraces   = useMemo(() => traces.filter((t) => t.mode === "real"),   [traces]);

  // Build shadow timeline client-side (backend timeline only has real-mode data)
  const shadowTimeline = useMemo(() => {
    const byContainer = {};
    shadowTraces.forEach((t) => {
      const c = t.container ?? "unknown";
      if (!byContainer[c]) byContainer[c] = [];
      byContainer[c].push({ time: t.trace_time ?? 0, score: t.score ?? 0 });
    });
    return byContainer;
  }, [shadowTraces]);

  // O(n) divergence detection via Map
  const divergences = useMemo(() => {
    const realMap = new Map();
    realTraces.forEach((r) => {
      const key = `${r.container}:${Math.round(r.trace_time ?? 0)}`;
      realMap.set(key, r);
    });
    return shadowTraces.filter((s) => {
      const key = `${s.container}:${Math.round(s.trace_time ?? 0)}`;
      const real = realMap.get(key);
      return real && real.action !== s.action;
    });
  }, [shadowTraces, realTraces]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 pb-2 border-b border-outline-variant">
        <span
          className="inline-block px-3 py-1 rounded text-sm font-label font-semibold"
          style={{ background: "#2e1b5e", color: "#a78bfa" }}
        >
          Shadow
        </span>
        <h2 className="font-headline font-bold text-xl" style={{ color: "#a78bfa" }}>
          Shadow Track
        </h2>
        <span className="text-sm text-outline ml-auto">
          {shadowTraces.length} shadow decisions · {divergences.length} divergences
        </span>
      </div>

      {/* Shadow info banner */}
      <div
        className="flex items-start gap-3 px-4 py-3 rounded-lg border"
        style={{ background: "#2e1b5e33", borderColor: "#a78bfa44" }}
      >
        <span className="material-symbols-outlined mt-0.5 flex-shrink-0" style={{ color: "#a78bfa" }}>
          visibility
        </span>
        <div>
          <p className="text-sm font-medium" style={{ color: "#a78bfa" }}>
            Shadow Mode — Dry-Run Parallel Path
          </p>
          <p className="text-xs text-outline mt-0.5">
            Shadow decisions are computed but not executed. Divergences occur when the shadow path
            would take a different action than the real path under the same conditions.
          </p>
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: "Shadow Decisions", value: shadowTraces.length, color: "#a78bfa", icon: "visibility" },
          { label: "Real Decisions",   value: realTraces.length,   color: "#4ade80",  icon: "check_circle" },
          { label: "Divergences",      value: divergences.length,  color: "#ffb4ab",  icon: "warning" },
          {
            label: "Divergence Rate",
            value: shadowTraces.length > 0
              ? `${((divergences.length / shadowTraces.length) * 100).toFixed(1)}%`
              : "—",
            color: "#eec200",
            icon: "percent",
          },
        ].map((card) => (
          <div key={card.label} className="glass-card rounded-lg p-4 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-label font-medium text-outline uppercase tracking-wider">
                {card.label}
              </span>
              <span className="material-symbols-outlined text-lg" style={{ color: card.color }}>
                {card.icon}
              </span>
            </div>
            <span className="font-headline text-2xl font-bold" style={{ color: card.color }}>
              {card.value}
            </span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2">
          <ScoreTimeline timeline={shadowTimeline} />
        </div>
        <ActionDistribution traces={shadowTraces} />
      </div>

      {/* Divergence log */}
      {divergences.length > 0 && (
        <div className="glass-card rounded-lg overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-outline-variant">
            <h3 className="font-headline font-semibold text-on-surface text-base">
              Divergences
            </h3>
            <span className="text-xs" style={{ color: "#ffb4ab" }}>
              {divergences.length} found
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-body">
              <thead>
                <tr className="border-b border-outline-variant bg-surface-container-low">
                  {["Container", "Track", "Shadow Action", "Real Action", "Score"].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left font-label font-medium text-outline uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {divergences.slice(0, 20).map((s, i) => {
                  const real = realTraces.find(
                    (r) => r.container === s.container && Math.abs((r.trace_time ?? 0) - (s.trace_time ?? 0)) < 2
                  );
                  return (
                    <tr key={i} className="border-b border-outline-variant hover:bg-surface-container-high transition-colors border-l-2 border-l-amber-400">
                      <td className="px-4 py-2.5 text-on-surface font-medium">{s.container}</td>
                      <td className="px-4 py-2.5">
                        <span
                          className="inline-flex items-center justify-center w-6 h-5 text-xs font-semibold rounded"
                          style={{
                            background: s.track === "S" ? "#1e3a5f" : "#2e1b5e",
                            color:      s.track === "S" ? "#60a5fa" : "#a78bfa",
                          }}
                        >
                          {s.track}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <span className="px-2 py-0.5 rounded text-xs font-label font-medium" style={{ background: "#a78bfa22", color: "#a78bfa" }}>
                          {(s.action ?? "").replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <span className="px-2 py-0.5 rounded text-xs font-label font-medium" style={{ background: "#4ade8022", color: "#4ade80" }}>
                          {(real?.action ?? "—").replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 font-semibold tabular-nums" style={{ color: "#eec200" }}>
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

function SettingsTab() {
  return (
    <div className="space-y-4">
      <h2 className="font-headline font-bold text-xl text-on-surface">Settings</h2>
      <div className="glass-card rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between py-3 border-b border-outline-variant">
          <div>
            <p className="text-sm font-medium text-on-surface">Refresh Interval</p>
            <p className="text-xs text-outline mt-0.5">How often the dashboard polls the backend</p>
          </div>
          <span className="text-sm text-primary-container font-semibold">4 seconds</span>
        </div>
        <div className="flex items-center justify-between py-3 border-b border-outline-variant">
          <div>
            <p className="text-sm font-medium text-on-surface">Backend URL</p>
            <p className="text-xs text-outline mt-0.5">Flask API endpoint</p>
          </div>
          <span className="text-sm text-on-surface-variant font-mono">localhost:5050</span>
        </div>
        <div className="flex items-center justify-between py-3">
          <div>
            <p className="text-sm font-medium text-on-surface">Trace Sources</p>
            <p className="text-xs text-outline mt-0.5">Files being watched</p>
          </div>
          <div className="text-right text-xs text-on-surface-variant font-mono">
            <p>stateless/traces.jsonl</p>
            <p>stateful/traces.jsonl</p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── App ─────────────────────────────────────────────────────── */

export default function App() {
  const [darkMode, setDarkMode]               = useState(true);
  const [searchQuery, setSearchQuery]         = useState("");
  const [activeTab, setActiveTab]             = useState("overview");
  const [modeFilter, setModeFilter]           = useState("all");
  const [selectedContainers, setSelectedContainers] = useState([]);

  const { traces, stats, containers, timeline, isLoading, isError } = useTraces();

  // Apply dark/light class to root
  React.useEffect(() => {
    const root = document.documentElement;
    if (darkMode) root.classList.add("dark");
    else root.classList.remove("dark");
  }, [darkMode]);

  // Unique container names for filter chips
  const allContainerNames = useMemo(
    () => [...new Set(traces.map((t) => t.container).filter(Boolean))].sort(),
    [traces]
  );

  // Apply global filters to traces
  const filteredTraces = useMemo(() => {
    let result = traces;
    if (modeFilter !== "all") {
      result = result.filter((t) => t.mode === modeFilter);
    }
    if (selectedContainers.length > 0) {
      result = result.filter((t) => selectedContainers.includes(t.container));
    }
    return result;
  }, [traces, modeFilter, selectedContainers]);

  // Apply container filter to containers list
  const filteredContainers = useMemo(() => {
    if (selectedContainers.length === 0) return containers;
    return containers.filter((c) => selectedContainers.includes(c.container));
  }, [containers, selectedContainers]);

  // Apply container filter to timeline
  const filteredTimeline = useMemo(() => {
    if (selectedContainers.length === 0) return timeline;
    return Object.fromEntries(
      Object.entries(timeline).filter(([k]) => selectedContainers.includes(k))
    );
  }, [timeline, selectedContainers]);

  const TAB_TITLES = {
    overview:  "System Overview",
    stateless: "Stateless Track",
    stateful:  "Stateful Track",
    shadow:    "Shadow Track",
    audit:     "Audit Log",
    settings:  "Settings",
  };

  const renderContent = () => {
    switch (activeTab) {
      case "overview":
        return (
          <OverviewTab
            stats={stats}
            traces={filteredTraces}
            timeline={filteredTimeline}
            containers={filteredContainers}
            isLoading={isLoading}
            searchQuery={searchQuery}
            setActiveTab={setActiveTab}
          />
        );
      case "stateless":
        return (
          <TrackTab
            traces={filteredTraces}
            timeline={filteredTimeline}
            containers={filteredContainers}
            isLoading={isLoading}
            searchQuery={searchQuery}
            track="S"
            setActiveTab={setActiveTab}
          />
        );
      case "stateful":
        return (
          <TrackTab
            traces={filteredTraces}
            timeline={filteredTimeline}
            containers={filteredContainers}
            isLoading={isLoading}
            searchQuery={searchQuery}
            track="F"
            setActiveTab={setActiveTab}
          />
        );
      case "shadow": {
        // Apply container filter but NOT mode filter — ShadowTab handles both modes internally
        const shadowTabTraces = selectedContainers.length > 0
          ? traces.filter((t) => selectedContainers.includes(t.container))
          : traces;
        return (
          <ShadowTab
            traces={shadowTabTraces}
            isLoading={isLoading}
            searchQuery={searchQuery}
            setActiveTab={setActiveTab}
          />
        );
      }
      case "audit":
        return (
          <div className="space-y-4">
            <h2 className="font-headline font-bold text-xl text-on-surface">Audit Log</h2>
            <AuditLog traces={filteredTraces} searchQuery={searchQuery} isLoading={isLoading} />
          </div>
        );
      case "settings":
        return <SettingsTab />;
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-surface text-on-surface">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        darkMode={darkMode}
        setDarkMode={setDarkMode}
      />

      {/* Main content area */}
      <div className="ml-64 flex flex-col min-h-screen">
        <TopBar
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          activeTab={activeTab}
          traces={filteredTraces}
        />

        <main className="flex-1 p-6 overflow-auto">
          {/* Connection error banner */}
          {isError && (
            <div className="mb-4 flex items-center gap-3 px-4 py-3 rounded-lg bg-error-container border border-error border-opacity-30">
              <span className="material-symbols-outlined text-error">error</span>
              <p className="text-sm text-error font-body">
                Cannot reach backend at <span className="font-mono">localhost:5050</span>. Start the Flask server:{" "}
                <span className="font-mono">python dashboard/backend.py</span>
              </p>
            </div>
          )}

          {/* Live indicator */}
          <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="font-headline font-bold text-2xl text-on-surface">
                {TAB_TITLES[activeTab] ?? activeTab}
              </h1>
              <p className="text-sm text-outline mt-0.5 font-body">
                UnderCurrent · Adaptive Control Plane · Live
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs text-outline">
              <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
              <span>Auto-refresh · 4s</span>
              {stats && (
                <span className="ml-2 px-2 py-0.5 rounded bg-surface-container border border-outline-variant text-on-surface-variant">
                  {stats.total?.toLocaleString()} total decisions
                </span>
              )}
              {modeFilter !== "all" && (
                <span
                  className="ml-1 px-2 py-0.5 rounded text-xs font-semibold"
                  style={
                    modeFilter === "real"
                      ? { background: "#4ade8033", color: "#4ade80", border: "1px solid #4ade8066" }
                      : { background: "#a78bfa33", color: "#a78bfa", border: "1px solid #a78bfa66" }
                  }
                >
                  {modeFilter} mode
                </span>
              )}
            </div>
          </div>

          {/* Global filter bar — hidden on settings */}
          {activeTab !== "settings" && (
            <FilterBar
              modeFilter={modeFilter}
              setModeFilter={setModeFilter}
              allContainerNames={allContainerNames}
              selectedContainers={selectedContainers}
              setSelectedContainers={setSelectedContainers}
            />
          )}

          {renderContent()}
        </main>

        {/* Footer */}
        <footer className="px-6 py-3 border-t border-outline-variant text-xs text-outline flex items-center justify-between no-print">
          <span>UnderCurrent Dashboard · {new Date().getFullYear()}</span>
          <span>Stateless S + Stateful F tracks · Flask 5050 → Vite 5173</span>
        </footer>
      </div>
    </div>
  );
}
