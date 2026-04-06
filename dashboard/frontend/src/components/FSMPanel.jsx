import React from "react";

const FSM_STATE_CONFIG = {
  Healthy:   { color: "#4ae183", icon: "check_circle",   label: "Healthy",   bg: "rgba(74,225,131,0.08)"  },
  Degraded:  { color: "#f4a52a", icon: "warning",        label: "Degraded",  bg: "rgba(244,165,42,0.08)"  },
  Audited:   { color: "#00daf3", icon: "manage_search",  label: "Audited",   bg: "rgba(0,218,243,0.08)"   },
  Repairing: { color: "#ffb4ab", icon: "build_circle",   label: "Repairing", bg: "rgba(255,180,171,0.08)" },
  Recovered: { color: "#c3c6d1", icon: "restore",        label: "Recovered", bg: "rgba(195,198,209,0.08)" },
};

const ACTION_ICONS = {
  no_action:               { icon: "check",         color: "#4ae183" },
  restart:                 { icon: "restart_alt",   color: "#f4a52a" },
  reschedule:              { icon: "schedule",      color: "#c3c6d1" },
  escalate:                { icon: "priority_high", color: "#ffb4ab" },
  flush_io_queue:          { icon: "water_drop",    color: "#00daf3" },
  checkpoint_and_restart:  { icon: "save",          color: "#f97316" },
};

function scoreColor(score) {
  if (score >= 0.95) return "#ffb4ab";
  if (score >= 0.80) return "#f4a52a";
  if (score >= 0.50) return "#f4d03f";
  return "#4ae183";
}

function ContainerCard({ container }) {
  const { container: name, track, score, action, mode, fsm_state } = container;
  const fsmCfg  = FSM_STATE_CONFIG[fsm_state];
  const sc      = scoreColor(score ?? 0);
  const actCfg  = ACTION_ICONS[action] ?? { icon: "circle", color: "#8b90a0" };
  const borderL = fsmCfg ? fsmCfg.color : (track === "S" ? "#00daf3" : "#c3c6d1");

  return (
    <div
      className="bg-surface-container rounded-2xl p-6 border border-outline-variant/20 hover:bg-surface-container-high transition-all duration-200 group flex flex-col gap-4"
      style={{ borderLeftColor: borderL, borderLeftWidth: 3 }}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-surface-container-lowest flex items-center justify-center group-hover:scale-110 transition-transform"
               style={{ boxShadow: `0 0 10px ${borderL}22` }}>
            <span className="material-symbols-outlined text-xl" style={{ color: borderL,
              fontVariationSettings: "'FILL' 1" }}>
              {track === "F" ? "database" : "dataset"}
            </span>
          </div>
          <div>
            <p className="font-headline font-bold text-sm text-on-surface truncate max-w-[120px]">{name}</p>
            <p className="text-[10px] text-on-surface-variant mt-0.5">
              {track === "F" ? "Stateful (F)" : "Stateless (S)"}
            </p>
          </div>
        </div>

        {/* FSM / status badge */}
        {fsmCfg ? (
          <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider flex-shrink-0"
                style={{ background: fsmCfg.bg, color: fsmCfg.color, border: `1px solid ${fsmCfg.color}33` }}>
            {fsmCfg.label}
          </span>
        ) : (
          <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider flex-shrink-0"
                style={{ background: "rgba(0,218,243,0.08)", color: "#00daf3", border: "1px solid rgba(0,218,243,0.2)" }}>
            {track === "S" ? "Stateless" : "Active"}
          </span>
        )}
      </div>

      {/* Score bar */}
      <div>
        <div className="flex justify-between items-center mb-1.5">
          <span className="text-[10px] text-on-surface-variant uppercase tracking-wider">Risk Score</span>
          <span className="text-sm font-bold tabular-nums" style={{ color: sc }}>{(score ?? 0).toFixed(3)}</span>
        </div>
        <div className="w-full h-1.5 bg-surface-container-lowest rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all duration-500"
               style={{ width: `${Math.min((score ?? 0) * 100, 100)}%`, background: sc,
                        boxShadow: score >= 0.8 ? `0 0 8px ${sc}88` : "none" }} />
        </div>
      </div>

      {/* Action + Mode */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="material-symbols-outlined text-sm" style={{ color: actCfg.color }}>
            {actCfg.icon}
          </span>
          <span className="text-xs font-medium" style={{ color: actCfg.color }}>
            {(action ?? "").replace(/_/g, " ")}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${mode === "real" ? "bg-tertiary" : "bg-outline"}`} />
          <span className="text-[10px] text-on-surface-variant capitalize">{mode}</span>
        </div>
      </div>
    </div>
  );
}

export default function FSMPanel({ containers, onViewAll }) {
  if (!containers || containers.length === 0) {
    return (
      <div className="bg-surface-container rounded-2xl p-8 border border-outline-variant/20">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h3 className="font-headline font-bold text-lg text-on-surface">Container States</h3>
            <p className="text-xs text-on-surface-variant mt-0.5">Live FSM health per container</p>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-44 rounded-2xl skeleton" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface-container rounded-2xl p-8 border border-outline-variant/20">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-headline font-bold text-lg text-on-surface">Container States</h3>
          <p className="text-xs text-on-surface-variant mt-0.5">Live FSM health per container</p>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-xs text-on-surface-variant">{containers.length} containers</span>
          {onViewAll && (
            <button onClick={onViewAll}
                    className="text-xs font-bold text-primary flex items-center gap-1 hover:opacity-80 transition-opacity">
              VIEW ALL
              <span className="material-symbols-outlined text-sm">arrow_forward</span>
            </button>
          )}
        </div>
      </div>

      {/* FSM state legend */}
      <div className="flex flex-wrap gap-2 mb-6">
        {Object.entries(FSM_STATE_CONFIG).map(([state, cfg]) => (
          <span key={state} className="text-[10px] px-2.5 py-1 rounded-full font-label font-medium"
                style={{ background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.color}33` }}>
            {cfg.label}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 max-h-[520px] overflow-y-auto pr-1">
        {containers.map((c) => (
          <ContainerCard key={`${c.container}-${c.track}`} container={c} />
        ))}
      </div>
    </div>
  );
}
