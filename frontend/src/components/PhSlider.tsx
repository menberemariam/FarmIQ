"use client";

import { Droplets } from "lucide-react";
import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";

const PH_GRADIENT = "linear-gradient(to right, #ef4444, #f5f5f4 48%, #f5f5f4 52%, #14532d)";

interface PhSliderProps {
  value: number;
  onChange: (value: number) => void;
  lang: Lang;
}

export default function PhSlider({ value, onChange, lang }: PhSliderProps) {
  const min = 3.5;
  const max = 9.9;

  return (
    <div className="rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-xl bg-forest text-white">
            <Droplets size={20} />
          </span>
          <div>
            <p className="text-sm font-semibold text-stone-800">{t(lang, "soilPh")}</p>
            <p className="text-xs text-stone-400">0.0 – 14.0</p>
          </div>
        </div>
        <input
          type="number"
          min={0}
          max={14}
          step={0.1}
          value={value}
          onChange={(e) =>
            onChange(Math.min(14, Math.max(0, Number(e.target.value) || 0)))
          }
          className="w-20 rounded-xl border border-stone-200 bg-stone-50 px-2 py-1.5 text-right text-sm font-semibold text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
        />
      </div>
      <div className="relative mt-5">
        <div
          className="absolute inset-y-0 left-0 right-0 rounded-full"
          style={{ background: PH_GRADIENT }}
        />
        <input
          type="range"
          className="ph-track relative"
          style={{
            ["--thumb-color" as string]: "#14532d",
          }}
          min={min}
          max={max}
          step={0.1}
          value={Math.min(max, Math.max(min, value))}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label={t(lang, "soilPh")}
        />
      </div>
      <div className="mt-2 flex justify-between text-[11px] font-medium text-stone-400">
        <span className="text-bad">{t(lang, "acidic")}</span>
        <span>{t(lang, "neutral")}</span>
        <span className="text-forest">{t(lang, "alkaline")}</span>
      </div>
    </div>
  );
}
