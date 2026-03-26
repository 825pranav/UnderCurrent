/**
 * Convert an array of trace objects to CSV and trigger download.
 */
export function exportCSV(traces) {
  if (!traces || traces.length === 0) return;

  const columns = [
    "trace_time", "container", "track", "score", "action",
    "mode", "why", "executed", "fsm_state", "fsm_transition",
    "reversibility", "dag_pattern",
  ];

  const escape = (val) => {
    if (val === null || val === undefined) return "";
    const str = String(val).replace(/"/g, '""');
    return str.includes(",") || str.includes('"') || str.includes("\n")
      ? `"${str}"`
      : str;
  };

  const header = columns.join(",");
  const rows = traces.map((t) =>
    columns.map((col) => escape(t[col])).join(",")
  );

  const csv = [header, ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href      = url;
  link.download  = `undercurrent_traces_${Date.now()}.csv`;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Trigger browser print dialog (can be saved as PDF).
 */
export function exportPDF() {
  window.print();
}
