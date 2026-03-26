import React, { useState, useMemo } from "react";
import { format } from "date-fns";
import SkeletonRow from "./SkeletonRow.jsx";

const PAGE_SIZE = 15;

const ACTION_COLORS = {
  no_action:               { bg: "#1e3a5f22", text: "#60a5fa" },
  restart:                 { bg: "#42350022", text: "#eec200" },
  reschedule:              { bg: "#2d1f5e22", text: "#cebdff" },
  escalate:                { bg: "#4a000022", text: "#ffb4ab" },
  flush_io_queue:          { bg: "#00404422", text: "#22d3ee" },
  checkpoint_and_restart:  { bg: "#4a200022", text: "#f97316" },
};

function scoreColor(score) {
  if (score >= 0.95) return "#ffb4ab";
  if (score >= 0.8)  return "#fb923c";
  if (score >= 0.5)  return "#eec200";
  return "#4ade80";
}

function SortIcon({ col, sortCol, sortDir }) {
  if (sortCol !== col) return <span className="material-symbols-outlined text-sm text-outline opacity-40">unfold_more</span>;
  return (
    <span className="material-symbols-outlined text-sm text-primary-container">
      {sortDir === "asc" ? "arrow_upward" : "arrow_downward"}
    </span>
  );
}

export default function AuditLog({ traces, searchQuery, isLoading }) {
  const [sortCol, setSortCol]   = useState("trace_time");
  const [sortDir, setSortDir]   = useState("desc");
  const [page, setPage]         = useState(1);

  // Reset to page 1 whenever the search query changes
  React.useEffect(() => { setPage(1); }, [searchQuery]);

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
    setPage(1);
  };

  const filtered = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return traces;
    return traces.filter((t) =>
      (t.container ?? "").toLowerCase().includes(q) ||
      (t.action    ?? "").toLowerCase().includes(q) ||
      (t.mode      ?? "").toLowerCase().includes(q) ||
      (t.track     ?? "").toLowerCase().includes(q)
    );
  }, [traces, searchQuery]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let va = a[sortCol] ?? "";
      let vb = b[sortCol] ?? "";
      if (typeof va === "number" && typeof vb === "number") {
        return sortDir === "asc" ? va - vb : vb - va;
      }
      va = String(va).toLowerCase();
      vb = String(vb).toLowerCase();
      return sortDir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    });
  }, [filtered, sortCol, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageRows = sorted.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const cols = [
    { key: "trace_time", label: "Time" },
    { key: "track",      label: "Track" },
    { key: "container",  label: "Container" },
    { key: "score",      label: "Score" },
    { key: "action",     label: "Action" },
    { key: "mode",       label: "Mode" },
    { key: "why",        label: "Why" },
  ];

  return (
    <div className="glass-card rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-outline-variant">
        <h3 className="font-headline font-semibold text-on-surface text-base">Audit Log</h3>
        <span className="text-xs text-outline">
          {filtered.length} entries
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-body">
          <thead>
            <tr className="border-b border-outline-variant bg-surface-container-low">
              {cols.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="px-4 py-2.5 text-left font-label font-medium text-outline uppercase tracking-wider cursor-pointer select-none hover:text-on-surface transition-colors whitespace-nowrap"
                >
                  <div className="flex items-center gap-1">
                    {col.label}
                    <SortIcon col={col.key} sortCol={sortCol} sortDir={sortDir} />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading
              ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={7} />)
              : pageRows.map((t, i) => {
                  const isDivergence = t.mode === "shadow" && t.action !== "no_action";
                  const ac = ACTION_COLORS[t.action] ?? { bg: "#2a292e", text: "#c1c7d3" };
                  const sc = scoreColor(t.score ?? 0);

                  return (
                    <tr
                      key={i}
                      className={`border-b border-outline-variant hover:bg-surface-container-high transition-colors ${
                        isDivergence ? "border-l-2 border-l-tertiary" : ""
                      }`}
                    >
                      {/* Time */}
                      <td className="px-4 py-2.5 text-outline whitespace-nowrap">
                        {t.trace_time
                          ? format(new Date(t.trace_time * 1000), "HH:mm:ss.SSS")
                          : "—"}
                      </td>

                      {/* Track badge */}
                      <td className="px-4 py-2.5">
                        <span
                          className="inline-flex items-center justify-center w-6 h-5 text-xs font-semibold rounded"
                          style={{
                            background: t.track === "S" ? "#1e3a5f" : "#2e1b5e",
                            color:      t.track === "S" ? "#60a5fa" : "#a78bfa",
                          }}
                        >
                          {t.track}
                        </span>
                      </td>

                      {/* Container */}
                      <td className="px-4 py-2.5 text-on-surface font-medium whitespace-nowrap">
                        {t.container}
                      </td>

                      {/* Score */}
                      <td className="px-4 py-2.5 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <div className="w-14 h-1.5 bg-surface-container-high rounded-full overflow-hidden flex-shrink-0">
                            <div
                              className="h-full rounded-full"
                              style={{
                                width: `${Math.min((t.score ?? 0) * 100, 100)}%`,
                                background: sc,
                              }}
                            />
                          </div>
                          <span style={{ color: sc }} className="font-semibold tabular-nums">
                            {(t.score ?? 0).toFixed(3)}
                          </span>
                        </div>
                      </td>

                      {/* Action chip */}
                      <td className="px-4 py-2.5 whitespace-nowrap">
                        <span
                          className="inline-block px-2 py-0.5 rounded text-xs font-label font-medium"
                          style={{ background: ac.bg, color: ac.text }}
                        >
                          {(t.action ?? "").replace(/_/g, " ")}
                        </span>
                      </td>

                      {/* Mode */}
                      <td className="px-4 py-2.5 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          {t.mode === "real" ? (
                            <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
                          ) : (
                            <span
                              className="w-2 h-2 rounded-full flex-shrink-0"
                              style={{ border: "1px dashed #8b919d" }}
                            />
                          )}
                          <span className={`capitalize ${t.mode === "real" ? "text-green-400" : "text-outline"}`}>
                            {t.mode}
                          </span>
                        </div>
                      </td>

                      {/* Why */}
                      <td className="px-4 py-2.5 max-w-xs">
                        <span
                          className="block truncate text-on-surface-variant"
                          title={t.why ?? ""}
                        >
                          {t.why ?? "—"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between px-5 py-3 border-t border-outline-variant">
        <span className="text-xs text-outline">
          Page {currentPage} of {totalPages} &nbsp;·&nbsp; {sorted.length} total
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={currentPage === 1}
            className="px-2 py-1 rounded bg-surface-container border border-outline-variant text-on-surface-variant text-xs disabled:opacity-30 hover:bg-surface-container-high transition-colors"
          >
            <span className="material-symbols-outlined text-sm">chevron_left</span>
          </button>
          {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
            const p = Math.max(1, Math.min(currentPage - 2, totalPages - 4)) + i;
            return (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={`w-7 h-7 rounded text-xs font-label font-medium transition-colors ${
                  p === currentPage
                    ? "bg-primary-container text-on-primary"
                    : "bg-surface-container border border-outline-variant text-on-surface-variant hover:bg-surface-container-high"
                }`}
              >
                {p}
              </button>
            );
          })}
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={currentPage === totalPages}
            className="px-2 py-1 rounded bg-surface-container border border-outline-variant text-on-surface-variant text-xs disabled:opacity-30 hover:bg-surface-container-high transition-colors"
          >
            <span className="material-symbols-outlined text-sm">chevron_right</span>
          </button>
        </div>
      </div>
    </div>
  );
}
