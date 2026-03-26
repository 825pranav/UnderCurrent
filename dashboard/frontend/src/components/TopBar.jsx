import React from "react";
import { exportCSV, exportPDF } from "../utils/export.js";

const TAB_LABELS = {
  overview:  "Overview",
  stateless: "Stateless Track",
  stateful:  "Stateful Track",
  audit:     "Audit Log",
  settings:  "Settings",
};

export default function TopBar({ searchQuery, setSearchQuery, activeTab, traces }) {
  return (
    <header className="h-14 bg-surface-container-low border-b border-outline-variant flex items-center px-6 gap-4 sticky top-0 z-30 no-print">
      {/* Breadcrumb */}
      <div className="flex items-center gap-1 text-sm font-body text-outline flex-shrink-0">
        <span className="text-on-surface-variant">Dashboard</span>
        <span className="material-symbols-outlined text-sm text-outline">chevron_right</span>
        <span className="text-on-surface font-medium">{TAB_LABELS[activeTab] ?? activeTab}</span>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Search */}
      <div className="relative w-64">
        <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline text-lg pointer-events-none">
          search
        </span>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search containers, actions…"
          className="w-full bg-surface-container border border-outline-variant rounded pl-9 pr-3 py-1.5 text-sm text-on-surface placeholder-outline focus:outline-none focus:border-primary-container transition-colors"
        />
      </div>

      {/* Export CSV */}
      <button
        onClick={() => exportCSV(traces)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-container border border-outline-variant text-on-surface-variant hover:text-on-surface hover:border-outline text-sm font-body transition-colors"
      >
        <span className="material-symbols-outlined text-base">download</span>
        CSV
      </button>

      {/* Export PDF */}
      <button
        onClick={exportPDF}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-container border border-outline-variant text-on-surface-variant hover:text-on-surface hover:border-outline text-sm font-body transition-colors"
      >
        <span className="material-symbols-outlined text-base">picture_as_pdf</span>
        PDF
      </button>

      {/* User avatar */}
      <div className="w-8 h-8 rounded-full bg-secondary-container flex items-center justify-center flex-shrink-0 cursor-pointer">
        <span className="material-symbols-outlined text-secondary text-base">person</span>
      </div>
    </header>
  );
}
