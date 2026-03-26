import React from "react";
import { BarChart, Bar, ResponsiveContainer, Cell } from "recharts";

function MiniSparkline({ data, color }) {
  if (!data || data.length === 0) return <div className="h-8" />;
  return (
    <ResponsiveContainer width="100%" height={32}>
      <BarChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
        <Bar dataKey="score" radius={[1, 1, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={color} fillOpacity={0.6 + (i / data.length) * 0.4} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function KPICard({ label, value, accent, icon, sparkData, trend }) {
  return (
    <div className="glass-card rounded-lg p-4 flex flex-col gap-3 relative overflow-hidden">
      {/* top row */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-label font-medium text-on-surface-variant uppercase tracking-wider">{label}</span>
        <span className="material-symbols-outlined text-lg" style={{ color: accent }}>{icon}</span>
      </div>

      {/* number */}
      <div className="flex items-end justify-between">
        <span className="font-headline text-3xl font-bold text-on-surface" style={{ color: accent }}>
          {value}
        </span>
        {trend !== undefined && (
          <span className="text-xs px-1.5 py-0.5 rounded bg-surface-container-high text-on-surface-variant">
            last cycle
          </span>
        )}
      </div>

      {/* sparkline */}
      <MiniSparkline data={sparkData} color={accent} />
    </div>
  );
}

export default function KPIStrip({ stats, timeline }) {
  if (!stats) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="glass-card rounded-lg p-4 h-32 skeleton" />
        ))}
      </div>
    );
  }

  // Build sparkline data from timeline (all containers, last 5 points combined avg)
  const buildSparkData = () => {
    const allPoints = Object.values(timeline).flat();
    allPoints.sort((a, b) => a.time - b.time);
    const last = allPoints.slice(-15);
    // bucket into 5 groups
    const buckets = [];
    const size = Math.max(1, Math.ceil(last.length / 5));
    for (let i = 0; i < 5; i++) {
      const chunk = last.slice(i * size, (i + 1) * size);
      const avg = chunk.length ? chunk.reduce((s, p) => s + p.score, 0) / chunk.length : 0;
      buckets.push({ score: avg });
    }
    return buckets;
  };

  const sparkData = buildSparkData();

  const peakScore = stats.peak_score ?? 0;
  const peakColor =
    peakScore > 0.8 ? "#ffb4ab" :
    peakScore > 0.5 ? "#eec200" :
    "#4ade80";

  const cards = [
    {
      label:     "Total Decisions",
      value:     stats.total?.toLocaleString() ?? "—",
      accent:    "#60a5fa",
      icon:      "analytics",
      sparkData,
    },
    {
      label:     "Restarts",
      value:     stats.restarts?.toLocaleString() ?? "—",
      accent:    "#eec200",
      icon:      "restart_alt",
      sparkData: sparkData.map((d) => ({ score: d.score * 0.3 })),
    },
    {
      label:     "Reschedules",
      value:     stats.reschedules?.toLocaleString() ?? "—",
      accent:    "#cebdff",
      icon:      "schedule",
      sparkData: sparkData.map((d) => ({ score: d.score * 0.5 })),
    },
    {
      label:     "No-Actions",
      value:     stats.no_actions?.toLocaleString() ?? "—",
      accent:    "#60a5fa",
      icon:      "check_circle",
      sparkData: sparkData.map((d) => ({ score: d.score * 0.8 })),
    },
    {
      label:     "Peak Risk Score",
      value:     peakScore.toFixed(2),
      accent:    peakColor,
      icon:      "warning",
      sparkData: sparkData.map((d) => ({ score: d.score })),
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
      {cards.map((card) => (
        <KPICard key={card.label} {...card} />
      ))}
    </div>
  );
}
