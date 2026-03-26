import React, { useMemo } from "react";
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";

const ACTION_COLORS = {
  no_action:               "#60a5fa",
  restart:                 "#eec200",
  reschedule:              "#cebdff",
  escalate:                "#ffb4ab",
  flush_io_queue:          "#22d3ee",
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
    <div className="bg-surface-container border border-outline-variant rounded-lg px-3 py-2 text-xs font-body shadow-xl">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full" style={{ background: p.fill }} />
        <span className="text-on-surface font-semibold">{name}</span>
      </div>
      <p className="text-on-surface-variant mt-1">{value} decisions ({p.pct}%)</p>
    </div>
  );
}

function CenterLabel({ cx, cy, total }) {
  return (
    <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central">
      <tspan x={cx} dy="-6" fontSize="22" fontWeight="700" fill="#e4e1e7" fontFamily="Space Grotesk">
        {total}
      </tspan>
      <tspan x={cx} dy="20" fontSize="10" fill="#8b919d" fontFamily="Inter">
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
        fill: ACTION_COLORS[action] ?? "#8b919d",
        pct: total ? Math.round((count / total) * 100) : 0,
      }))
      .sort((a, b) => b.value - a.value);
    return { data, total };
  }, [traces]);

  if (traces.length === 0) {
    return (
      <div className="glass-card rounded-lg p-4 h-72 flex items-center justify-center text-outline text-sm">
        No data
      </div>
    );
  }

  return (
    <div className="glass-card rounded-lg p-4">
      <h3 className="font-headline font-semibold text-on-surface text-base mb-4">Action Distribution</h3>
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="45%"
            innerRadius={65}
            outerRadius={95}
            paddingAngle={2}
            dataKey="value"
            isAnimationActive={false}
          >
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.fill} />
            ))}
          </Pie>

          {/* Center label via custom label */}
          <Pie
            data={[{ value: 1 }]}
            cx="50%"
            cy="45%"
            innerRadius={0}
            outerRadius={0}
            dataKey="value"
            label={(props) => <CenterLabel {...props} total={total} />}
            labelLine={false}
            isAnimationActive={false}
          />

          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 11, fontFamily: "Inter", color: "#c1c7d3" }}
            formatter={(val, entry) => (
              <span style={{ color: "#c1c7d3" }}>
                {val} <span style={{ color: "#8b919d" }}>({entry.payload.pct}%)</span>
              </span>
            )}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
