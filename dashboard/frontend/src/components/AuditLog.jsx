import React, { useState, useMemo } from "react";
import { format } from "date-fns";
import SkeletonRow from "./SkeletonRow.jsx";

const PAGE_SIZE = 15;

const ACTION_COLORS = {
  no_action:               { bg: "rgba(0,218,243,0.08)",   text: "#00daf3" },
  restart:                 { bg: "rgba(244,165,42,0.1)",   text: "#f4a52a" },
  reschedule:              { bg: "rgba(195,198,209,0.1)",  text: "#c3c6d1" },
  escalate:                { bg: "rgba(255,180,171,0.1)",  text: "#ffb4ab" },
  flush_io_queue:          { bg: "rgba(74,225,131,0.08)",  text: "#4ae183" },
  checkpoint_and_restart:  { bg: "rgba(249,115,22,0.1)",   text: "#f97316" },
};

const ACTION_ICONS = {
  no_action:               "check_circle",
  restart:                 "restart_alt",
  reschedule:              "schedule",
  escalate:                "warning",
  flush_io_queue:          "water_drop",
  checkpoint_and_restart:  "save",
};

function scoreColor(score) {
  if (score >= 0.95) return "#ffb4ab";
  if (score >= 0.80) return "#f4a52a";
  if (score >= 0.50) return "#f4d03f";
  return "#4ae183";
}

function SortIcon({ col, sortCol, sortDir }) {
  if (sortCol !== col)
    return <span className="material-symbols-outlined text-sm text-on-surface-variant opacity-30">unfold_more</span>;
  return (
    <span className="material-symbols-outlined text-sm text-primary">
      {sortDir === "asc" ? "arrow_upward" : "arrow_downward"}
    </span>
  );
}

export default function AuditLog({ traces, searchQuery, isLoading }) {
  const [sortCol, setSortCol] = useState("trace_time");
  const [sortDir, setSortDir] = useState("desc");
  const [page, setPage]       = useState(1);

  React.useEffect(() => { setPage(1); }, [searchQuery]);

  const handleSort = (col) => {
    if (sortCol === col) setSortDir((d) => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("asc"); }
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
      let va = a[sortCol] ?? "", vb = b[sortCol] ?? "";
      if (typeof va === "number" && typeof vb === "number")
        return sortDir === "asc" ? va - vb : vb - va;
      va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
      return sortDir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    });
  }, [filtered, sortCol, sortDir]);

  const totalPages  = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageRows    = sorted.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const cols = [
    { key: "trace_time", label: "Timestamp" },
    { key: "track",      label: "Engine" },
    { key: "container",  label: "Container" },
    { key: "score",      label: "Risk Score" },
    { key: "action",     label: "Action Taken" },
    { key: "mode",       label: "Mode" },
    { key: "why",        label: "Reason" },
  ];

  return (
    <div className="bg-surface-container rounded-2xl border border-outline-variant/20 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-8 py-5 border-b border-outline-variant/20">
        <div>
          <h3 className="font-headline font-bold text-lg text-on-surface">Decision Audit Log</h3>
          <p className="text-xs text-on-surface-variant mt-0.5">{filtered.length} entries</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-on-surface-variant">
          <span className="material-symbols-outlined text-sm text-primary">verified</span>
          Append-only · tamper-evident
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-surface-container-low/60 border-b border-outline-variant/20">
              {cols.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="px-6 py-4 text-[10px] font-label font-medium text-on-surface-variant uppercase tracking-widest cursor-pointer select-none hover:text-on-surface transition-colors whitespace-nowrap"
                >
                  <div className="flex items-center gap-1">
                    {col.label}
                    <SortIcon col={col.key} sortCol={sortCol} sortDir={sortDir} />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-outline-variant/10">
            {isLoading
              ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={7} />)
              : pageRows.map((t, i) => {
                  const ac  = ACTION_COLORS[t.action] ?? { bg: "rgba(139,144,160,0.1)", text: "#8b90a0" };
                  const sc  = scoreColor(t.score ?? 0);
                  const ai  = ACTION_ICONS[t.action] ?? "circle";
                  const isHigh = (t.score ?? 0) >= 0.8 && t.action !== "no_action";

                  return (
                    <tr key={i}
                        className={`hover:bg-surface-container-high transition-colors cursor-pointer
                          ${isHigh ? "border-l-2 border-l-error" : ""}`}>

                      {/* Timestamp */}
                      <td className="px-6 py-4 text-xs font-label text-on-surface-variant whitespace-nowrap">
                        {t.trace_time
                          ? format(new Date(t.trace_time * 1000), "yyyy-MM-dd HH:mm:ss")
                          : "—"}
                      </td>

                      {/* Engine / track */}
                      <td className="px-6 py-4">
                        <span className="inline-flex items-center justify-center w-7 h-6 text-[10px] font-bold rounded"
                              style={{
                                background: t.track === "S" ? "rgba(0,218,243,0.12)" : "rgba(195,198,209,0.12)",
                                color:      t.track === "S" ? "#00daf3" : "#c3c6d1",
                              }}>
                          {t.track ?? "—"}
                        </span>
                      </td>

                      {/* Container */}
                      <td className="px-6 py-4 text-sm font-bold text-primary whitespace-nowrap">
                        {t.container ?? "—"}
                      </td>

                      {/* Risk Score */}
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-1.5 bg-surface-container-lowest rounded-full overflow-hidden flex-shrink-0">
                            <div className="h-full rounded-full transition-all"
                                 style={{ width: `${Math.min((t.score ?? 0) * 100, 100)}%`, background: sc }} />
                          </div>
                          <span className="text-xs font-bold tabular-nums" style={{ color: sc }}>
                            {(t.score ?? 0).toFixed(3)}
                          </span>
                        </div>
                      </td>

                      {/* Action */}
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <span className="material-symbols-outlined text-base" style={{ color: ac.text }}>
                            {ai}
                          </span>
                          <span className="inline-block px-2 py-0.5 rounded text-xs font-label font-bold"
                                style={{ background: ac.bg, color: ac.text }}>
                            {(t.action ?? "").replace(/_/g, " ")}
                          </span>
                        </div>
                      </td>

                      {/* Mode */}
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          {t.mode === "real"
                            ? <span className="w-2 h-2 rounded-full bg-tertiary" />
                            : <span className="w-2 h-2 rounded-full border border-dashed border-outline" />
                          }
                          <span className={`text-xs capitalize ${t.mode === "real" ? "text-tertiary" : "text-on-surface-variant"}`}>
                            {t.mode ?? "—"}
                          </span>
                        </div>
                      </td>

                      {/* Why */}
                      <td className="px-6 py-4 max-w-xs">
                        <span className="block truncate text-xs text-on-surface-variant" title={t.why ?? ""}>
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
      <div className="flex items-center justify-between px-8 py-4 border-t border-outline-variant/20">
        <span className="text-xs text-on-surface-variant">
          Page {currentPage} of {totalPages} &nbsp;·&nbsp; {sorted.length} total
        </span>
        <div className="flex items-center gap-1">
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={currentPage === 1}
                  className="px-2 py-1 rounded-lg bg-surface-container-low border border-outline-variant/30 text-on-surface-variant text-xs disabled:opacity-30 hover:bg-surface-container-high transition-colors">
            <span className="material-symbols-outlined text-sm">chevron_left</span>
          </button>
          {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
            const p = Math.max(1, Math.min(currentPage - 2, totalPages - 4)) + i;
            return (
              <button key={p} onClick={() => setPage(p)}
                      className={`w-8 h-8 rounded-lg text-xs font-label font-bold transition-colors ${
                        p === currentPage
                          ? "text-on-primary"
                          : "bg-surface-container-low border border-outline-variant/30 text-on-surface-variant hover:bg-surface-container-high"
                      }`}
                      style={p === currentPage ? { background: "linear-gradient(135deg,#00daf3,#009fb2)" } : {}}>
                {p}
              </button>
            );
          })}
          <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages}
                  className="px-2 py-1 rounded-lg bg-surface-container-low border border-outline-variant/30 text-on-surface-variant text-xs disabled:opacity-30 hover:bg-surface-container-high transition-colors">
            <span className="material-symbols-outlined text-sm">chevron_right</span>
          </button>
        </div>
      </div>
    </div>
  );
}
