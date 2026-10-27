"use client";

import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";
import type { SuitabilityClass } from "@/lib/api";

const LIGHT_STYLES: Record<SuitabilityClass, string> = {
  S1: "border-emerald-200 bg-emerald-50 text-emerald-800",
  S2: "border-amber-200 bg-amber-50 text-amber-800",
  S3: "border-stone-200 bg-stone-100 text-stone-600",
  N: "border-red-200 bg-red-50 text-red-700",
};

const DARK_STYLES: Record<SuitabilityClass, string> = {
  S1: "border-white/25 bg-white/15 text-white",
  S2: "border-gold/40 bg-gold/20 text-gold",
  S3: "border-white/15 bg-white/10 text-white/70",
  N: "border-red-300/40 bg-red-500/20 text-red-100",
};

const LABEL_KEYS: Record<SuitabilityClass, string> = {
  S1: "suitS1",
  S2: "suitS2",
  S3: "suitS3",
  N: "suitN",
};

interface SuitabilityChipProps {
  cls: SuitabilityClass;
  lang: Lang;
  dark?: boolean;
}

export default function SuitabilityChip({ cls, lang, dark = false }: SuitabilityChipProps) {
  const styles = dark ? DARK_STYLES : LIGHT_STYLES;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${styles[cls]}`}
      aria-label={`${cls} — ${t(lang, LABEL_KEYS[cls])}`}
    >
      <span aria-hidden>{cls}</span>
      <span aria-hidden>·</span>
      <span aria-hidden>{t(lang, LABEL_KEYS[cls])}</span>
    </span>
  );
}
