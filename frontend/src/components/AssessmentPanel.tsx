"use client";

import { Info } from "lucide-react";
import ActionPlan from "@/components/ActionPlan";
import FertilityGauge from "@/components/FertilityGauge";
import { t, statusLabel } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type { DeviceAssessment } from "@/lib/api";

export default function AssessmentPanel({
  assessment,
}: {
  assessment: DeviceAssessment;
}) {
  const { lang } = useLang();
  const warnings = assessment.warnings ?? [];
  const crops = assessment.crops.slice(0, 3);

  return (
    <section>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-display text-xl font-semibold text-stone-900">
          {t(lang, "iotAssessment")}
        </h2>
        <span className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-0.5 text-[11px] font-medium text-sky-700">
          {assessment.simulated ? t(lang, "iotSimulatedBadge") : assessment.source}
        </span>
      </div>

      <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start">
        <FertilityGauge
          status={assessment.fertility_status}
          confidence={assessment.confidence}
          statusLabel={statusLabel(lang, assessment.fertility_status)}
          confidenceLabel={t(lang, "confidence")}
        />

        <div className="flex-1 space-y-4">
          <div className="rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
            <p className="text-sm font-semibold text-stone-800">
              {t(lang, "bestMatch")}
            </p>
            <ul className="mt-2 space-y-1.5">
              {crops.map((crop, i) => (
                <li
                  key={crop.crop}
                  className="flex items-baseline justify-between gap-2 text-sm"
                >
                  <span
                    className={`${i === 0 ? "font-semibold text-forest" : "text-stone-600"}`}
                  >
                    {i === 0 ? "★ " : ""}
                    {crop.crop}
                  </span>
                  <span className="text-xs tabular-nums text-stone-400">
                    {crop.confidence}%
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {warnings.length > 0 && (
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
              <p className="inline-flex items-center gap-2 text-sm font-semibold text-amber-800">
                <Info size={14} /> {t(lang, "inputWarnings")}
              </p>
              <ul className="mt-1.5 space-y-1 text-xs text-amber-700">
                {warnings.map((w) => (
                  <li key={`${w.field}-${w.message}`}>{w.message}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      <div className="mt-4">
        <ActionPlan recommendations={assessment.recommendations} lang={lang} />
      </div>
    </section>
  );
}
