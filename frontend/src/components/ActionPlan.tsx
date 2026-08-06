"use client";

import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";
import type { Recommendation } from "@/lib/api";

const BULLET: Record<string, string> = {
  Nitrogen: "#14532d",
  Phosphorus: "#92400e",
  Potassium: "#d97706",
  pH: "#059669",
};

interface ActionPlanProps {
  recommendations: Recommendation[];
  lang: Lang;
}

export default function ActionPlan({ recommendations, lang }: ActionPlanProps) {
  return (
    <div className="rounded-2xl bg-[#f3efe7] p-6 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <h3 className="font-display text-xl font-semibold text-stone-900">
        {t(lang, "actionPlan")}
      </h3>
      <ul className="mt-4 space-y-4">
        {recommendations.map((rec) => (
          <li key={rec.element} className="flex gap-3">
            <span
              className="mt-1.5 size-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: BULLET[rec.element] ?? "#14532d" }}
            />
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-stone-800">{rec.element}</p>
                <span className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-stone-500">
                  {rec.source === "llm" ? t(lang, "aiValidated") : t(lang, "calculated")}
                </span>
              </div>
              <p className="mt-1 text-sm leading-relaxed text-stone-600">{rec.solution}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
