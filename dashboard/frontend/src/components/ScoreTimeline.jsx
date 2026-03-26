import React, { useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ReferenceLine, ResponsiveContainer,
} from "recharts";
import { format } from "date-fns";

const CONTAINER_COLORS = [
  "#60a5fa", "#a78bfa", "#34d399", "#fb923c", "#f472b6",
  "#38bdf8", "#facc15", "#4ade80", "#f87171", "#e879f9",
];

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="bg-surface-container border border-outline-variant rounded-lg px-3 py-2 text-xs font-body shadow-xl">
      <p className="text-outline mb-1">{label}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 py-0.5">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: p.color }} />
          <span className="text-on-surface-variant">{p.name}:</span>
          <span className="text-on-surface font-semibold">{Number(p.value).toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

export default function ScoreTimeline({ timeline }) {
  const { chartData, containerNames } = useMemo(() => {
    const names = Object.keys(timeline);
    if (names.length === 0) return { chartData: [], containerNames: [] };

    // Merge all timestamps
    const timeSet = new Set();
    names.forEach((c) =>
      timeline[c].forEach((p) => timeSet.add(Math.round(p.time)))
    );
    const times = Array.from(timeSet).sort((a, b) => a - b);

    // Latest value per container at each timestamp (forward-fill)
    const latestByContainer = {};
    names.forEach((c) => (latestByContainer[c] = 0));

    // Build lookup: container -> sorted points
    const sorted = {};
    names.forEach((c) => {
      sorted[c] = [...timeline[c]].sort((a, b) => a.time - b.time);
    });

    // Pointers
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

    // Downsample to max 200 points
    const step = Math.max(1, Math.floor(rows.length / 200));
    const sampled = rows.filter((_, i) => i % step === 0);

    return { chartData: sampled, containerNames: names };
  }, [timeline]);

  if (chartData.length === 0) {
    return (
      <div className="glass-card rounded-lg p-4 h-72 flex items-center justify-center text-outline text-sm">
        No timeline data available
      </div>
    );
  }

  return (
    <div className="glass-card rounded-lg p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-headline font-semibold text-on-surface text-base">Risk Score Timeline</h3>
        <div className="flex items-center gap-3 text-xs font-label text-outline">
          <span className="flex items-center gap-1">
            <span className="w-6 border-t-2 border-dashed border-red-400 inline-block" />
            Escalate 0.95
          </span>
          <span className="flex items-center gap-1">
            <span className="w-6 border-t-2 border-dashed border-orange-400 inline-block" />
            Restart 0.80
          </span>
          <span className="flex items-center gap-1">
            <span className="w-6 border-t border-dotted border-outline inline-block" />
            Flush 0.50
          </span>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#414751" strokeDasharray="3 3" strokeOpacity={0.5} />
          <XAxis
            dataKey="time"
            tick={{ fill: "#8b919d", fontSize: 10, fontFamily: "Inter" }}
            tickLine={false}
            axisLine={{ stroke: "#414751" }}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fill: "#8b919d", fontSize: 10, fontFamily: "Inter" }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => v.toFixed(1)}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 11, fontFamily: "Inter", color: "#c1c7d3", paddingTop: 8 }}
          />

          {/* Reference lines */}
          <ReferenceLine y={0.95} stroke="#f87171" strokeDasharray="4 3" strokeWidth={1.5} label={{ value: "Escalate", fill: "#f87171", fontSize: 10, position: "right" }} />
          <ReferenceLine y={0.80} stroke="#fb923c" strokeDasharray="4 3" strokeWidth={1.5} label={{ value: "Restart",  fill: "#fb923c", fontSize: 10, position: "right" }} />
          <ReferenceLine y={0.50} stroke="#8b919d" strokeDasharray="2 4" strokeWidth={1}   label={{ value: "Flush",   fill: "#8b919d", fontSize: 10, position: "right" }} />

          {containerNames.map((c, i) => (
            <Line
              key={c}
              type="monotone"
              dataKey={c}
              stroke={CONTAINER_COLORS[i % CONTAINER_COLORS.length]}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
