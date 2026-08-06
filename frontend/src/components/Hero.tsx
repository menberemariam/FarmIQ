"use client";

import { Sprout } from "lucide-react";
import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";

const LEAF_PATTERN =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120' viewBox='0 0 120 120'>
      <g fill='none' stroke='#ffffff' stroke-width='1.2' opacity='0.14'>
        <path d='M60 20 C 80 35, 80 70, 60 100 C 40 70, 40 35, 60 20 Z'/>
        <path d='M60 20 C 60 50, 60 70, 60 100'/>
        <circle cx='60' cy='60' r='34'/>
        <circle cx='60' cy='60' r='18'/>
      </g>
    </svg>`
  );

interface HeroProps {
  lang: Lang;
  onStart: () => void;
}

export default function Hero({ lang, onStart }: HeroProps) {
  return (
    <header className="relative overflow-hidden bg-gradient-to-b from-forest to-forest-2 text-white">
      <div
        aria-hidden
        className="absolute inset-0"
        style={{ backgroundImage: `url("${LEAF_PATTERN}")`, backgroundSize: "120px 120px" }}
      />
      <div className="relative mx-auto max-w-4xl px-6 py-20 text-center sm:py-28">
        <span className="inline-flex items-center gap-2 rounded-full border border-white/25 bg-white/10 px-4 py-1.5 text-sm font-medium tracking-wide backdrop-blur-sm">
          {t(lang, "badge")}
        </span>
        <h1 className="font-display mt-6 text-4xl font-semibold leading-tight sm:text-6xl">
          {t(lang, "heroTitle")}
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-base leading-relaxed text-white/85 sm:text-lg">
          {t(lang, "heroSubtext")}
        </p>
        <button
          onClick={onStart}
          className="mt-9 inline-flex items-center gap-2 rounded-2xl bg-gold px-7 py-3.5 text-base font-semibold text-white shadow-lg transition hover:brightness-110 active:scale-[0.98]"
        >
          <Sprout size={20} />
          {t(lang, "ctaAnalyze")}
        </button>
      </div>
    </header>
  );
}
