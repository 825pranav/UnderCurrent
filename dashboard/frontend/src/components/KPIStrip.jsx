import React from "react";
import { BarChart, Bar, ResponsiveContainer, Cell } from "recharts";

function MiniSparkline({ data, color }) {
  if (!data || data.length === 0) return <div className="h-8" />;
  return (
    <ResponsiveContainer width="100%" height={32}>
      <BarChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
        <Bar dataKey="score" radius={[2, 2, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={color} fillOpacity={0.4 + (i / data.length) * 0.6} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function KPICard({ label, value, accent, icon, sparkData, sub }) {
  return (
    <div className="bg-surface-container rounded-2xl p-6 border border-outline-variant/20 flex flex-col gap-3 hover:bg-surface-container-high transition-all duration-200 group">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-label font-medium text-on-surface-variant uppercase tracking-widest">
          {label}
        </span>
        <div className="w-9 h-9 rounded-xl bg-surface-container-lowest flex items-center justify-center group-hover:scale-110 transition-transform"
             style={{ boxShadow: `0 0 12px ${accent}22` }}>
          <span className="material-symbols-outlined text-lg" style={{ color: accent }}>
            {icon}
          </span>
        </div>
      </div>

      <div>
        <span className="font-headline text-3xl font-extrabold" style={{ color: accent }}>
          {value}
        </span>
        {sub && (
          <p className="text-xs text-on-surface-variant mt-0.5">{sub}</p>
        )}
      </div>

      <MiniSparkline data={sparkData} color={accent} />
    </div>
  );
}

export default function KPIStrip({ stats, timeline }) {
  if (!stats) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-6">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="bg-surface-container rounded-2xl p-6 h-36 skeleton" />
        ))}
      </div>
    );
  }

  const buildSparkData = () => {
    const allPoints = Object.values(timeline).flat();
    allPoints.sort((a, b) => a.time - b.time);
    const last = allPoints.slice(-15);
    const size = Math.max(1, Math.ceil(last.length / 5));
    return Array.from({ length: 5 }, (_, i) => {
      const chunk = last.slice(i * size, (i + 1) * size);
      const avg = chunk.length ? chunk.reduce((s, p) => s + p.score, 0) / chunk.length : 0;
      return { score: avg };
    });
  };

  const sparkData = buildSparkData();
  const peakScore = stats.peak_score ?? 0;
  const peakColor = peakScore > 0.8 ? "#ffb4ab" : peakScore > 0.5 ? "#f4a52a" : "#4ae183";

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-6">
      <KPICard
        label="Total Decisions"
        value={stats.total?.toLocaleString() ?? "—"}
        accent="#00daf3"
        icon="analytics"
        sparkData={sparkData}
        sub="all tracks combined"
      />
      <KPICard
        label="Restarts"
        value={stats.restarts?.toLocaleString() ?? "—"}
        accent="#f4a52a"
        icon="restart_alt"
        sparkData={sparkData.map((d) => ({ score: d.score * 0.3 }))}
        sub="stateless track"
      />
      <KPICard
        label="Reschedules"
        value={stats.reschedules?.toLocaleString() ?? "—"}
        accent="#c3c6d1"
        icon="schedule"
        sparkData={sparkData.map((d) => ({ score: d.score * 0.5 }))}
        sub="stateless track"
      />
      <KPICard
        label="No-Actions"
        value={stats.no_actions?.toLocaleString() ?? "—"}
        accent="#4ae183"
        icon="check_circle"
        sparkData={sparkData.map((d) => ({ score: d.score * 0.8 }))}
        sub="all tracks"
      />
      <KPICard
        label="Peak Risk Score"
        value={peakScore.toFixed(2)}
        accent={peakColor}
        icon="warning"
        sparkData={sparkData}
        sub={peakScore >= 0.8 ? "⚠ elevated" : "within normal range"}
      />
    </div>
  );
}
