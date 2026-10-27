"use client";

import { CloudRain, Sun } from "lucide-react";
import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";
import type { CropMatch } from "@/lib/api";
import SuitabilityChip from "@/components/SuitabilityChip";

interface CropMatchCardProps {
  match: CropMatch;
  lang: Lang;
  confidenceLabel: string;
  tempLabel: string;
  rainfallLabel: string;
}

export default function CropMatchCard({
  match,
  lang,
  confidenceLabel,
  tempLabel,
  rainfallLabel,
}: CropMatchCardProps) {
  return (
    <div className="animate-fade-up overflow-hidden rounded-2xl bg-gradient-to-br from-forest to-forest-2 text-white shadow-xl">
      <div className="p-7">
        <span className="inline-block rounded-full bg-white/15 px-3 py-1 text-xs font-semibold uppercase tracking-wider">
          {t(lang, "bestMatch")}
        </span>
        <h2 className="font-display mt-4 text-4xl font-semibold capitalize sm:text-5xl">
          {match.crop}
        </h2>
        <div className="mt-2 flex items-center gap-3">
          {match.scientific_name && (
            <p className="text-sm italic text-white/70">{match.scientific_name}</p>
          )}
          {match.suitability && (
            <SuitabilityChip cls={match.suitability.class} lang={lang} dark />
          )}
        </div>

        <div className="mt-6">
          <div className="flex items-center justify-between text-sm">
            <span className="text-white/75">{confidenceLabel}</span>
            <span className="font-semibold tabular-nums">{match.confidence.toFixed(0)}%</span>
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-white/20">
            <div
              className="h-2 rounded-full bg-gold transition-all duration-700"
              style={{ width: `${match.confidence}%` }}
            />
          </div>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3">
          <div className="flex items-center gap-3 rounded-2xl bg-white/10 p-4">
            <Sun size={20} className="text-gold" />
            <div>
              <p className="text-[11px] uppercase tracking-wider text-white/60">
                {tempLabel}
              </p>
              <p className="font-display text-xl font-semibold tabular-nums">
                {match.temperature}°C
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3 rounded-2xl bg-white/10 p-4">
            <CloudRain size={20} className="text-sky-300" />
            <div>
              <p className="text-[11px] uppercase tracking-wider text-white/60">
                {rainfallLabel}
              </p>
              <p className="font-display text-xl font-semibold tabular-nums">
                {match.rainfall} mm
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
