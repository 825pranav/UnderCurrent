import React from "react";

const FSM_STATE_CONFIG = {
  Healthy:   { color: "#4ade80", bg: "bg-green-950",  label: "Healthy" },
  Degraded:  { color: "#eec200", bg: "bg-yellow-950", label: "Degraded" },
  Audited:   { color: "#a78bfa", bg: "bg-purple-950", label: "Audited" },
  Repairing: { color: "#f87171", bg: "bg-red-950",    label: "Repairing" },
  Recovered: { color: "#60a5fa", bg: "bg-blue-950",   label: "Recovered" },
};

function scoreColor(score) {
  if (score >= 0.95) return "#ffb4ab";
  if (score >= 0.8)  return "#fb923c";
  if (score >= 0.5)  return "#eec200";
  return "#4ade80";
}

function actionChipColor(action) {
  const map = {
    no_action:              "#60a5fa",
    restart:                "#eec200",
    reschedule:             "#cebdff",
    escalate:               "#ffb4ab",
    flush_io_queue:         "#22d3ee",
    checkpoint_and_restart: "#f97316",
  };
  return map[action] ?? "#8b919d";
}

function ContainerCard({ container }) {
  const { container: name, track, score, action, mode, fsm_state } = container;
  const fsmCfg = FSM_STATE_CONFIG[fsm_state];
  const borderColor = fsmCfg ? fsmCfg.color : (track === "S" ? "#60a5fa" : "#a78bfa");
  const sc = scoreColor(score);

  return (
    <div
      className="bg-surface-container rounded-lg p-4 border border-outline-variant flex flex-col gap-3"
      style={{ borderLeftColor: borderColor, borderLeftWidth: 3 }}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-headline font-semibold text-on-surface text-sm truncate">{name}</p>
          <p className="text-xs text-outline mt-0.5">
            {track === "F" ? "Stateful (F)" : "Stateless (S)"}
          </p>
        </div>
        {/* Track badge */}
        <span
          className="text-xs font-label font-semibold px-2 py-0.5 rounded flex-shrink-0"
          style={{
            background: track === "S" ? "#1e3a5f" : "#2e1b5e",
            color:       track === "S" ? "#60a5fa" : "#a78bfa",
          }}
        >
          {track}
        </span>
      </div>

      {/* FSM state badge or score bar */}
      {fsmCfg ? (
        <div className="flex items-center gap-2">
          <span
            className="text-xs font-label font-medium px-2 py-0.5 rounded"
            style={{ background: fsmCfg.color + "22", color: fsmCfg.color, border: `1px solid ${fsmCfg.color}44` }}
          >
            {fsmCfg.label}
          </span>
        </div>
      ) : (
        <div>
          <div className="h-1 bg-surface-container-high rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${Math.min(score * 100, 100)}%`, background: sc }}
            />
          </div>
        </div>
      )}

      {/* Score + Action */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="material-symbols-outlined text-sm" style={{ color: sc }}>
            {score >= 0.8 ? "warning" : score >= 0.5 ? "info" : "check_circle"}
          </span>
          <span className="text-sm font-semibold" style={{ color: sc }}>
            {score.toFixed(3)}
          </span>
        </div>
        <span
          className="text-xs font-label font-medium px-2 py-0.5 rounded truncate max-w-[120px]"
          style={{ background: actionChipColor(action) + "22", color: actionChipColor(action) }}
        >
          {action.replace(/_/g, " ")}
        </span>
      </div>

      {/* Mode */}
      <div className="flex items-center gap-1.5">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${mode === "real" ? "bg-green-400" : "bg-outline"}`}
          style={mode === "shadow" ? { border: "1px dashed #8b919d", background: "transparent" } : {}}
        />
        <span className="text-xs text-outline capitalize">{mode}</span>
      </div>
    </div>
  );
}

export default function FSMPanel({ containers, onViewAll }) {
  if (!containers || containers.length === 0) {
    return (
      <div className="glass-card rounded-lg p-4">
        <h3 className="font-headline font-semibold text-on-surface text-base mb-4">Container States</h3>
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-36 rounded-lg skeleton" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="glass-card rounded-lg p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-headline font-semibold text-on-surface text-base">Container States</h3>
        <div className="flex items-center gap-3">
          <span className="text-xs text-outline">{containers.length} containers</span>
          {onViewAll && (
            <button
              onClick={onViewAll}
              className="text-xs font-bold text-primary-container flex items-center gap-1 hover:text-primary transition-colors"
            >
              VIEW ALL
              <span className="material-symbols-outlined text-sm">arrow_forward</span>
            </button>
          )}
        </div>
      </div>

      {/* FSM legend */}
      <div className="flex flex-wrap gap-2 mb-4">
        {Object.entries(FSM_STATE_CONFIG).map(([state, cfg]) => (
          <span
            key={state}
            className="text-xs px-2 py-0.5 rounded font-label"
            style={{ background: cfg.color + "18", color: cfg.color, border: `1px solid ${cfg.color}33` }}
          >
            {cfg.label}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[480px] overflow-y-auto pr-1">
        {containers.map((c) => (
          <ContainerCard key={c.container} container={c} />
        ))}
      </div>
    </div>
  );
}
