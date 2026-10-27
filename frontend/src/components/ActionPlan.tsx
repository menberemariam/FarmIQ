"use client";

import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";
import type { LimePlan, Recommendation } from "@/lib/api";

const BULLET: Record<string, string> = {
  Nitrogen: "#14532d",
  Phosphorus: "#92400e",
  Potassium: "#d97706",
  pH: "#059669",
};

interface ActionPlanProps {
  recommendations: Recommendation[];
  lang: Lang;
  limePlan?: LimePlan | null;
  streaming?: boolean;
  validatedCount?: number;
  totalCount?: number;
}

export default function ActionPlan({
  recommendations,
  lang,
  limePlan,
  streaming = false,
  validatedCount = 0,
  totalCount = 0,
}: ActionPlanProps) {
  return (
    <div className="rounded-2xl bg-[#f3efe7] p-6 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <h3 className="font-display text-xl font-semibold text-stone-900">
        {t(lang, "actionPlan")}
      </h3>
      {streaming && (
        <p className="mt-1 flex items-center gap-2 text-xs font-medium text-forest">
          <span className="size-1.5 animate-pulse rounded-full bg-forest" />
          {t(lang, "streamingWriting")}
          {totalCount > 0 && (
            <span className="text-stone-400">
              · {validatedCount}/{totalCount} {t(lang, "streamingValidated")}
            </span>
          )}
        </p>
      )}
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
      {limePlan && limePlan.total_t_ha > 0 && (
        <div className="mt-5 rounded-xl border border-forest/15 bg-white p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-stone-800">
              {t(lang, "limePlanTitle")}
            </p>
            <span className="rounded-full bg-forest/10 px-2 py-0.5 text-[11px] font-medium text-forest">
              {lang === "om"
                ? limePlan.name_om
                : lang === "am"
                  ? limePlan.name_am
                  : limePlan.name_en}
            </span>
            <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[11px] font-medium text-stone-500">
              {t(lang, "limePlanLrf")} {limePlan.lrf} · {limePlan.ecce_pct}% ECCE
            </span>
          </div>
          <p className="mt-2 text-sm text-stone-600">
            {t(lang, "limePlanTotal")}: <strong>{limePlan.total_t_ha} t/ha</strong>
            {limePlan.lime_type === "dolomitic" ? " · dolomitic ×0.84" : ""}
          </p>
          <ul className="mt-2 space-y-1">
            {limePlan.schedule.map((entry) => (
              <li key={entry.season} className="text-sm text-stone-600">
                {t(lang, "limePlanPerSeason")} {entry.season}:{" "}
                <strong>{entry.t_ha} t/ha</strong>
              </li>
            ))}
          </ul>
          {limePlan.split_required && (
            <p className="mt-2 text-xs text-stone-400">
              ≤ {limePlan.max_single_t_ha} t/ha per season — Ethiopian single-application ceiling.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
