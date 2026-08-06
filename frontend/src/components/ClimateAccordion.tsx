"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";
import type { ClimateValues } from "@/lib/api";

interface ClimateAccordionProps {
  climate: ClimateValues;
  onChange: (climate: ClimateValues) => void;
  lang: Lang;
  onRefresh: () => void;
  refreshing: boolean;
}

function ClimateRow({
  label,
  value,
  min,
  max,
  unit,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  unit: string;
  step?: number;
  onChange: (v: number) => void;
}) {
  const fill = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-stone-700">{label}</span>
        <span className="font-semibold tabular-nums text-stone-900">
          {value}
          <span className="ml-0.5 text-xs font-normal text-stone-400">{unit}</span>
        </span>
      </div>
      <input
        type="range"
        className="soil-slider mt-2"
        style={{
          ["--thumb-color" as string]: "#14532d",
          background: `linear-gradient(to right, #14532d ${fill}%, #e7e5e4 ${fill}%)`,
        }}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={label}
      />
    </div>
  );
}

export default function ClimateAccordion({
  climate,
  onChange,
  lang,
  onRefresh,
  refreshing,
}: ClimateAccordionProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-2xl bg-gray-100 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
        aria-expanded={open}
      >
        <div>
          <p className="text-sm font-semibold text-stone-800">{t(lang, "climate")}</p>
          <p className="text-xs text-stone-500">{t(lang, "climateHint")}</p>
        </div>
        <ChevronDown
          size={20}
          className={`text-stone-500 transition-transform duration-300 ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="space-y-4 border-t border-stone-200 px-5 py-4">
          <ClimateRow
            label={t(lang, "temp")}
            value={climate.temperature}
            min={0}
            max={50}
            unit="°C"
            onChange={(v) => onChange({ ...climate, temperature: v })}
          />
          <ClimateRow
            label={t(lang, "humidity")}
            value={climate.humidity}
            min={0}
            max={100}
            unit="%"
            onChange={(v) => onChange({ ...climate, humidity: v })}
          />
          <ClimateRow
            label={t(lang, "rainfall")}
            value={climate.rainfall}
            min={0}
            max={400}
            unit="mm"
            onChange={(v) => onChange({ ...climate, rainfall: v })}
          />
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="w-full rounded-xl bg-forest py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-60"
          >
            {refreshing ? "…" : t(lang, "refreshCrops")}
          </button>
        </div>
      )}
    </div>
  );
}
