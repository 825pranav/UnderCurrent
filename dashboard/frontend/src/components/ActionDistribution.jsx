import React, { useMemo } from "react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";

const ACTION_COLORS = {
  no_action:               "#00daf3",
  restart:                 "#f4a52a",
  reschedule:              "#c3c6d1",
  escalate:                "#ffb4ab",
  flush_io_queue:          "#4ae183",
  checkpoint_and_restart:  "#f97316",
};

const ACTION_LABELS = {
  no_action:               "No Action",
  restart:                 "Restart",
  reschedule:              "Reschedule",
  escalate:                "Escalate",
  flush_io_queue:          "Flush IO",
  checkpoint_and_restart:  "Checkpoint",
};

function CustomTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const { name, value, payload: p } = payload[0];
  return (
    <div className="obsidian-glass rounded-xl px-3 py-2 text-xs font-body shadow-2xl border border-outline-variant/30">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full" style={{ background: p.fill }} />
        <span className="text-on-surface font-bold">{name}</span>
      </div>
      <p className="text-on-surface-variant mt-1">{value} &nbsp;·&nbsp; {p.pct}%</p>
    </div>
  );
}

function CenterLabel({ cx, cy, total }) {
  return (
    <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central">
      <tspan x={cx} dy="-6" fontSize="22" fontWeight="800" fill="#e1e2eb" fontFamily="Manrope">
        {total}
      </tspan>
      <tspan x={cx} dy="20" fontSize="10" fill="#8b90a0" fontFamily="Inter">
        decisions
      </tspan>
    </text>
  );
}

export default function ActionDistribution({ traces }) {
  const { data, total } = useMemo(() => {
    const counts = {};
    traces.forEach((t) => {
      const a = t.action ?? "no_action";
      counts[a] = (counts[a] ?? 0) + 1;
    });
    const total = traces.length;
    const data = Object.entries(counts)
      .map(([action, count]) => ({
        name: ACTION_LABELS[action] ?? action,
        value: count,
        fill: ACTION_COLORS[action] ?? "#8b90a0",
        pct: total ? Math.round((count / total) * 100) : 0,
        action,
      }))
      .sort((a, b) => b.value - a.value);
    return { data, total };
  }, [traces]);

  if (traces.length === 0) {
    return (
      <div className="bg-surface-container rounded-2xl p-8 border border-outline-variant/20 flex flex-col items-center justify-center gap-3" style={{ minHeight: 360 }}>
        <span className="material-symbols-outlined text-4xl text-on-surface-variant opacity-30">donut_large</span>
        <p className="text-on-surface-variant text-sm">No data yet</p>
      </div>
    );
  }

  return (
    <div className="bg-surface-container rounded-2xl p-8 border border-outline-variant/20 flex flex-col">
      <div className="mb-4">
        <h3 className="font-headline font-bold text-lg text-on-surface">Action Distribution</h3>
        <p className="text-xs text-on-surface-variant mt-0.5">Decision types by frequency</p>
      </div>

      {/* Donut */}
      <div className="flex-1 flex items-center justify-center">
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={65}
              outerRadius={90}
              paddingAngle={3}
              dataKey="value"
              isAnimationActive={false}
            >
              {data.map((entry, i) => (
                <Cell key={i} fill={entry.fill} stroke="transparent" />
              ))}
            </Pie>
            {/* Center label hack */}
            <Pie
              data={[{ value: 1 }]}
              cx="50%" cy="50%"
              innerRadius={0} outerRadius={0}
              dataKey="value"
              label={(props) => <CenterLabel {...props} total={total} />}
              labelLine={false}
              isAnimationActive={false}
            />
            <Tooltip content={<CustomTooltip />} />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* Legend */}
      <div className="space-y-3 mt-2">
        {data.map((d) => (
          <div key={d.action} className="flex justify-between items-center">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: d.fill }} />
              <span className="text-xs text-on-surface-variant">{d.name}</span>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-20 h-1 bg-surface-container-lowest rounded-full overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${d.pct}%`, background: d.fill }} />
              </div>
              <span className="text-xs font-bold text-on-surface w-8 text-right tabular-nums">{d.pct}%</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
