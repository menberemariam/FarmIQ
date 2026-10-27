"use client";

export interface TrendPoint {
  ts: string;
  v: number;
}

interface TrendChartProps {
  title: string;
  unit: string;
  /** Newest-first (API order); rendered oldest → newest. */
  points: TrendPoint[];
  color?: string;
  emptyLabel: string;
}

const W = 300;
const H = 120;
const PAD = 12;

export default function TrendChart({
  title,
  unit,
  points,
  color = "#059669",
  emptyLabel,
}: TrendChartProps) {
  const series = [...points].reverse();

  if (series.length < 2) {
    return (
      <div className="rounded-2xl bg-white p-4 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
        <p className="text-xs font-medium text-stone-500">{title}</p>
        <p className="py-8 text-center text-xs text-stone-400">{emptyLabel}</p>
      </div>
    );
  }

  const values = series.map((p) => p.v);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = rawMax - rawMin || 1; // guard flat series
  const min = rawMin === rawMax ? rawMin - 1 : rawMin - span * 0.1;
  const max = rawMin === rawMax ? rawMax + 1 : rawMax + span * 0.1;

  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (series.length - 1);
  const y = (v: number) =>
    PAD + ((max - v) / (max - min)) * (H - 2 * PAD);

  const path = series
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`)
    .join(" ");
  const last = series[series.length - 1];

  return (
    <div className="rounded-2xl bg-white p-4 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-xs font-medium text-stone-500">{title}</p>
        <p className="font-display text-lg font-semibold tabular-nums text-stone-800">
          {last.v.toFixed(1)}
          <span className="ml-1 text-xs font-normal text-stone-400">{unit}</span>
        </p>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="mt-2 w-full"
        role="img"
        aria-label={`${title}: ${last.v.toFixed(1)} ${unit}`}
      >
        <path
          d={path}
          fill="none"
          stroke={color}
          strokeWidth={2.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        <circle cx={x(series.length - 1)} cy={y(last.v)} r={3.5} fill={color} />
      </svg>
      <div className="flex justify-between text-[10px] tabular-nums text-stone-400">
        <span>
          min {rawMin.toFixed(1)} · max {rawMax.toFixed(1)}
        </span>
        <span>
          {series.length} {title.toLowerCase()}
        </span>
      </div>
    </div>
  );
}
