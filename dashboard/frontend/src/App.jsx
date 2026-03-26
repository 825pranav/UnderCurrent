import React, { useState, useMemo } from "react";
import Sidebar from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import KPIStrip from "./components/KPIStrip.jsx";
import ScoreTimeline from "./components/ScoreTimeline.jsx";
import ActionDistribution from "./components/ActionDistribution.jsx";
import FSMPanel from "./components/FSMPanel.jsx";
import AuditLog from "./components/AuditLog.jsx";
import { useTraces } from "./hooks/useTraces.js";

/* ── Tab views ─────────────────────────────────────────────── */

function OverviewTab({ stats, traces, timeline, containers, isLoading, searchQuery }) {
  return (
    <div className="space-y-6">
      <KPIStrip stats={stats} timeline={timeline} />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2">
          <ScoreTimeline timeline={timeline} />
        </div>
        <ActionDistribution traces={traces} />
      </div>
      <FSMPanel containers={containers} />
      <AuditLog traces={traces} searchQuery={searchQuery} isLoading={isLoading} />
    </div>
  );
}

function TrackTab({ traces, timeline, containers, isLoading, searchQuery, track }) {
  const filteredTraces     = useMemo(() => traces.filter((t) => t.track === track), [traces, track]);
  const filteredContainers = useMemo(() => containers.filter((c) => c.track === track), [containers, track]);

  // Filter timeline to only containers in this track
  const filteredTimeline = useMemo(() => {
    const containerSet = new Set(filteredContainers.map((c) => c.container));
    return Object.fromEntries(
      Object.entries(timeline).filter(([k]) => containerSet.has(k))
    );
  }, [timeline, filteredContainers]);

  const label = track === "S" ? "Stateless" : "Stateful";
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
        <span className="text-sm text-outline ml-auto">{filteredTraces.length} decisions · {filteredContainers.length} containers</span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2">
          <ScoreTimeline timeline={filteredTimeline} />
        </div>
        <ActionDistribution traces={filteredTraces} />
      </div>
      <FSMPanel containers={filteredContainers} />
      <AuditLog traces={filteredTraces} searchQuery={searchQuery} isLoading={isLoading} />
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
  const [darkMode, setDarkMode]       = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab]     = useState("overview");

  const { traces, stats, containers, timeline, isLoading, isError } = useTraces();

  // Apply dark/light class to root
  React.useEffect(() => {
    const root = document.documentElement;
    if (darkMode) root.classList.add("dark");
    else root.classList.remove("dark");
  }, [darkMode]);

  const renderContent = () => {
    switch (activeTab) {
      case "overview":
        return (
          <OverviewTab
            stats={stats}
            traces={traces}
            timeline={timeline}
            containers={containers}
            isLoading={isLoading}
            searchQuery={searchQuery}
          />
        );
      case "stateless":
        return (
          <TrackTab
            traces={traces}
            timeline={timeline}
            containers={containers}
            isLoading={isLoading}
            searchQuery={searchQuery}
            track="S"
          />
        );
      case "stateful":
        return (
          <TrackTab
            traces={traces}
            timeline={timeline}
            containers={containers}
            isLoading={isLoading}
            searchQuery={searchQuery}
            track="F"
          />
        );
      case "audit":
        return (
          <div className="space-y-4">
            <h2 className="font-headline font-bold text-xl text-on-surface">Audit Log</h2>
            <AuditLog traces={traces} searchQuery={searchQuery} isLoading={isLoading} />
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
          traces={traces}
        />

        <main className="flex-1 p-6 overflow-auto">
          {/* Connection error banner */}
          {isError && (
            <div className="mb-4 flex items-center gap-3 px-4 py-3 rounded-lg bg-error-container border border-error border-opacity-30">
              <span className="material-symbols-outlined text-error">error</span>
              <p className="text-sm text-error font-body">
                Cannot reach backend at <span className="font-mono">localhost:5050</span>. Start the Flask server: <span className="font-mono">python dashboard/backend.py</span>
              </p>
            </div>
          )}

          {/* Live indicator */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="font-headline font-bold text-2xl text-on-surface">
                {activeTab === "overview"  ? "System Overview"     :
                 activeTab === "stateless" ? "Stateless Track"     :
                 activeTab === "stateful"  ? "Stateful Track"      :
                 activeTab === "audit"     ? "Audit Log"           :
                 "Settings"}
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
            </div>
          </div>

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
