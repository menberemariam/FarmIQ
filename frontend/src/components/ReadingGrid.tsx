"use client";

import { AlertTriangle } from "lucide-react";
import { dateLocale, t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type { IotReading } from "@/lib/api";

const TILES: {
  key: "ph" | "N" | "P" | "K" | "moisture" | "temperature";
  labelKey: string;
  unitKey?: string;
  color: string;
}[] = [
  { key: "ph", labelKey: "soilPh", color: "#059669" },
  { key: "N", labelKey: "nitrogen", unitKey: "kgPerHa", color: "#14532d" },
  { key: "P", labelKey: "phosphorus", unitKey: "kgPerHa", color: "#92400e" },
  { key: "K", labelKey: "potassium", unitKey: "kgPerHa", color: "#d97706" },
  { key: "moisture", labelKey: "iotMoisture", color: "#0284c7" },
  { key: "temperature", labelKey: "temp", color: "#dc2626" },
];

const FLAG_KEY: Record<string, string> = {
  STALE_TIMESTAMP: "iotFlagStale",
  SUDDEN_VALUE_CHANGE: "iotFlagJump",
};

function formatTs(iso: string | null | undefined, lang: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(dateLocale(lang), {
    dateStyle: "short",
    timeStyle: "medium",
  });
}

export default function ReadingGrid({ reading }: { reading: IotReading }) {
  const { lang } = useLang();

  return (
    <div className="rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-stone-800">
          {t(lang, "iotReadings")}
        </p>
        <p className="text-xs text-stone-400">
          {formatTs(reading.reading_ts, lang)}
        </p>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {TILES.map((tile) => (
          <div key={tile.key} className="rounded-xl border border-stone-100 p-3">
            <dt className="truncate text-[11px] text-stone-400">
              {t(lang, tile.labelKey)}
            </dt>
            <dd
              className="mt-1 font-display text-xl font-semibold tabular-nums"
              style={{ color: tile.color }}
            >
              {reading[tile.key].toFixed(1)}
              <span className="ml-1 text-[10px] font-normal text-stone-400">
                {tile.unitKey ? t(lang, tile.unitKey) : ""}
              </span>
            </dd>
          </div>
        ))}
      </dl>

      {reading.quality === "warning" && reading.flags.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <AlertTriangle size={14} className="text-amber-600" />
          {reading.flags.map((flag) => (
            <span
              key={flag}
              className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-[11px] font-medium text-amber-700"
            >
              {FLAG_KEY[flag] ? t(lang, FLAG_KEY[flag]) : flag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
