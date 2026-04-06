import React, { useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ReferenceLine, ResponsiveContainer, Area, AreaChart,
} from "recharts";
import { format } from "date-fns";

const CONTAINER_COLORS = [
  "#00daf3", "#4ae183", "#c3c6d1", "#f4a52a", "#f472b6",
  "#38bdf8", "#a78bfa", "#fb923c", "#f87171", "#34d399",
];

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="obsidian-glass rounded-xl px-4 py-3 text-xs font-body shadow-2xl border border-outline-variant/30">
      <p className="text-on-surface-variant mb-2 font-label">{label}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 py-0.5">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: p.color }} />
          <span className="text-on-surface-variant">{p.name}:</span>
          <span className="text-on-surface font-bold tabular-nums">{Number(p.value).toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

export default function ScoreTimeline({ timeline }) {
  const { chartData, containerNames } = useMemo(() => {
    const names = Object.keys(timeline);
    if (names.length === 0) return { chartData: [], containerNames: [] };

    const timeSet = new Set();
    names.forEach((c) => timeline[c].forEach((p) => timeSet.add(Math.round(p.time))));
    const times = Array.from(timeSet).sort((a, b) => a - b);

    const latestByContainer = {};
    names.forEach((c) => (latestByContainer[c] = 0));

    const sorted = {};
    names.forEach((c) => { sorted[c] = [...timeline[c]].sort((a, b) => a.time - b.time); });

    const ptrs = {};
    names.forEach((c) => (ptrs[c] = 0));

    const rows = times.map((t) => {
      const row = { time: format(new Date(t * 1000), "HH:mm:ss") };
      names.forEach((c) => {
        const pts = sorted[c];
        while (ptrs[c] < pts.length && Math.round(pts[ptrs[c]].time) <= t) {
          latestByContainer[c] = pts[ptrs[c]].score;
          ptrs[c]++;
        }
        row[c] = latestByContainer[c];
      });
      return row;
    });

    const step = Math.max(1, Math.floor(rows.length / 200));
    return { chartData: rows.filter((_, i) => i % step === 0), containerNames: names };
  }, [timeline]);

  if (chartData.length === 0) {
    return (
      <div className="bg-surface-container rounded-2xl p-8 border border-outline-variant/20 h-80 flex flex-col items-center justify-center gap-3">
        <span className="material-symbols-outlined text-4xl text-on-surface-variant opacity-30">show_chart</span>
        <p className="text-on-surface-variant text-sm">No timeline data yet</p>
      </div>
    );
  }

  return (
    <div className="bg-surface-container rounded-2xl p-8 border border-outline-variant/20">
      <div className="flex justify-between items-start mb-6">
        <div>
          <h3 className="font-headline font-bold text-lg text-on-surface">Risk Score Timeline</h3>
          <p className="text-xs text-on-surface-variant mt-0.5">Real-time anomaly detection trends</p>
        </div>
        <div className="flex flex-wrap gap-4 text-xs font-label text-on-surface-variant">
          <span className="flex items-center gap-1.5">
            <span className="w-6 border-t-2 border-dashed border-red-400 inline-block" />
            Escalate 0.95
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-6 border-t-2 border-dashed border-orange-400 inline-block" />
            Repair 0.80
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-6 border-t border-dotted border-outline inline-block" />
            Flush 0.50
          </span>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#414754" strokeDasharray="3 3" strokeOpacity={0.3} />
          <XAxis
            dataKey="time"
            tick={{ fill: "#8b90a0", fontSize: 10, fontFamily: "Inter" }}
            tickLine={false}
            axisLine={{ stroke: "#414754" }}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fill: "#8b90a0", fontSize: 10, fontFamily: "Inter" }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => v.toFixed(1)}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 11, fontFamily: "Inter", color: "#c1c6d7", paddingTop: 8 }}
          />
          <ReferenceLine y={0.95} stroke="#f87171" strokeDasharray="4 3" strokeWidth={1.5}
            label={{ value: "Escalate", fill: "#f87171", fontSize: 10, position: "right" }} />
          <ReferenceLine y={0.80} stroke="#f4a52a" strokeDasharray="4 3" strokeWidth={1.5}
            label={{ value: "Repair", fill: "#f4a52a", fontSize: 10, position: "right" }} />
          <ReferenceLine y={0.50} stroke="#8b90a0" strokeDasharray="2 4" strokeWidth={1}
            label={{ value: "Flush", fill: "#8b90a0", fontSize: 10, position: "right" }} />
          {containerNames.map((c, i) => (
            <Line
              key={c}
              type="monotone"
              dataKey={c}
              stroke={CONTAINER_COLORS[i % CONTAINER_COLORS.length]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
