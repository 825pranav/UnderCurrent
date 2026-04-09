import React from "react";
import { exportCSV, exportPDF } from "../utils/export.js";

const TAB_LABELS = {
  overview:  "Overview",
  stateless: "Stateless Track",
  stateful:  "Stateful Track",
  shadow:    "Shadow Track",
  audit:     "Audit Log",
  settings:  "Settings",
};

export default function TopBar({ searchQuery, setSearchQuery, activeTab, traces, stats }) {
  return (
    <header className="fixed top-0 right-0 w-[calc(100%-16rem)] z-40 h-20 flex items-center justify-between px-10 no-print"
            style={{ background: "rgba(16,19,26,0.85)", backdropFilter: "blur(20px)", borderBottom: "1px solid rgba(65,71,84,0.3)" }}>

      {/* Left: breadcrumb + search */}
      <div className="flex items-center gap-6">
        <nav className="flex items-center gap-1.5 text-xs font-label text-on-surface-variant hidden md:flex">
          <span className="opacity-60">Dashboard</span>
          <span className="material-symbols-outlined text-[10px] opacity-60">chevron_right</span>
          <span className="text-primary">{TAB_LABELS[activeTab] ?? activeTab}</span>
        </nav>

        <div className="relative">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant text-base pointer-events-none">
            search
          </span>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search containers, actions…"
            className="bg-surface-container-low border border-outline-variant rounded-full pl-10 pr-5 py-2 text-sm text-on-surface placeholder-on-surface-variant/40 focus:outline-none focus:ring-1 focus:ring-primary w-72 transition-all"
          />
        </div>
      </div>

      {/* Right: live indicator, export, icons */}
      <div className="flex items-center gap-4">
        {/* Live pulse */}
        <div className="flex items-center gap-2 text-xs text-on-surface-variant">
          <span className="w-2 h-2 rounded-full bg-tertiary animate-pulse" />
          <span className="hidden lg:inline">Live · 4s</span>
        </div>

        {stats && (
          <div className="hidden lg:flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-container border border-outline-variant text-xs text-on-surface-variant">
            <span className="material-symbols-outlined text-sm text-primary">analytics</span>
            <span className="font-semibold text-on-surface">{stats.total?.toLocaleString()}</span>
            <span>decisions</span>
          </div>
        )}

        <div className="h-5 w-px bg-outline-variant" />

        <button
          onClick={() => exportCSV(traces)}
          title="Export CSV"
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-surface-container border border-outline-variant text-on-surface-variant hover:text-on-surface hover:border-outline text-xs font-body transition-colors"
        >
          <span className="material-symbols-outlined text-base">download</span>
          <span className="hidden sm:inline">CSV</span>
        </button>

        <button
          onClick={exportPDF}
          title="Export PDF"
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-surface-container border border-outline-variant text-on-surface-variant hover:text-on-surface hover:border-outline text-xs font-body transition-colors"
        >
          <span className="material-symbols-outlined text-base">picture_as_pdf</span>
          <span className="hidden sm:inline">PDF</span>
        </button>

        <button className="flex items-center gap-2 px-4 py-2 rounded-lg text-on-primary text-sm font-bold active:scale-95 transition-transform"
                style={{ background: "linear-gradient(135deg, #00daf3, #009fb2)" }}>
          <span className="material-symbols-outlined text-base">file_export</span>
          <span className="hidden md:inline">Export</span>
        </button>
      </div>
    </header>
  );
}
