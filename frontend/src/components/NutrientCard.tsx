"use client";

import type { Recommendation } from "@/lib/api";

const STATUS_STYLE: Record<string, { text: string; bg: string; bar: string }> = {
  deficient: { text: "text-bad", bg: "bg-bad/10 border-bad/30", bar: "bg-bad" },
  ample: { text: "text-gold", bg: "bg-gold/10 border-gold/30", bar: "bg-gold" },
  adequate: { text: "text-good", bg: "bg-good/10 border-good/30", bar: "bg-good" },
};

interface NutrientCardProps {
  rec: Recommendation;
  index: number;
  idealLabel: string;
  currentLabel: string;
}

export default function NutrientCard({ rec, index, idealLabel, currentLabel }: NutrientCardProps) {
  const style = STATUS_STYLE[rec.status] ?? STATUS_STYLE.adequate;
  const hi = Number.parseFloat(rec.ideal_range.split("-")[1] ?? "100");
  const lo = Number.parseFloat(rec.ideal_range);
  const trackMax = hi * 1.35;
  const fillPct = Math.min((rec.value / trackMax) * 100, 100);
  const bandLo = (lo / trackMax) * 100;
  const bandHi = (hi / trackMax) * 100;

  return (
    <div
      className="animate-fade-up flex flex-col rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]"
      style={{ animationDelay: `${index * 20}ms` }}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-stone-800">{rec.element}</p>
          <p className="mt-0.5 text-xs text-stone-400">
            {idealLabel}: {rec.ideal_range}
          </p>
        </div>
        <span
          className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${style.text} ${style.bg}`}
        >
          {rec.status}
        </span>
      </div>

      <p className="font-display mt-3 text-3xl font-semibold text-stone-900">
        {rec.value}
        <span className="ml-1 text-sm font-normal text-stone-400">
          {rec.element === "pH" ? "pH" : "kg/ha"}
        </span>
      </p>

      <div className="relative mt-4 h-1.5 w-full rounded-full bg-stone-100">
        <div
          className={`animate-bar h-1.5 rounded-full ${style.bar}`}
          style={{ width: `${fillPct}%` }}
        />
        <div
          className="absolute top-0 h-1.5 w-0.5 rounded-full bg-forest/60"
          style={{ left: `${bandLo}%` }}
          title={rec.ideal_range}
        />
        <div
          className="absolute top-0 h-1.5 w-0.5 rounded-full bg-forest/60"
          style={{ left: `${bandHi}%` }}
          title={rec.ideal_range}
        />
      </div>
      <p className="mt-2 text-xs text-stone-400">
        {currentLabel}: {rec.value}
        {rec.deficit > 0 ? ` — ${rec.deficit.toFixed(1)} ${rec.element === "pH" ? "units" : "kg/ha"}` : ""}
      </p>
    </div>
  );
}
