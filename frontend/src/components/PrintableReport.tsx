"use client";

import type { Lang } from "@/i18n/strings";
import { dateLocale, t } from "@/i18n/strings";
import type { CostEstimate, ReportDetail } from "@/lib/api";

/** Maps engine element names and cost-line columns to i18n keys. */
const ELEMENT_KEY: Record<string, string> = {
  Nitrogen: "nitrogen",
  Phosphorus: "phosphorus",
  Potassium: "potassium",
  pH: "soilPh",
  N: "nitrogen",
  P: "phosphorus",
  K: "potassium",
  ph: "soilPh",
};

function formatDate(iso: string | null | undefined, lang: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(dateLocale(lang));
}

function statusChip(status: string): string {
  if (status === "Good" || status === "adequate") return "bg-good/10 text-good";
  if (status === "Moderate") return "bg-gold/10 text-soil";
  if (status === "ample") return "bg-stone-200 text-stone-600";
  return "bg-bad/10 text-bad";
}

function fertilityChip(status: string): string {
  if (status === "Good") return "bg-good/10 text-good";
  if (status === "Moderate") return "bg-gold/10 text-soil";
  return "bg-bad/10 text-bad";
}

interface PrintableReportProps {
  detail: ReportDetail;
  cost: CostEstimate;
  lang: Lang;
}

export default function PrintableReport({ detail, cost, lang }: PrintableReportProps) {
  const { report, outcome, farmer, plot } = detail;
  const simulated = report.source.startsWith("iot");
  const recs = report.recommendations ?? [];

  const identity: Array<{ label: string; value: string }> = [
    { label: t(lang, "farmerName"), value: farmer?.name ?? report.plot },
    { label: t(lang, "region"), value: farmer?.region ?? "—" },
    { label: t(lang, "plotName"), value: plot?.name ?? report.plot },
    {
      label: t(lang, "farmSize"),
      value: plot?.farm_size_ha != null ? `${plot.farm_size_ha} ha` : "—",
    },
    { label: t(lang, "sampleDate"), value: formatDate(report.created_at, lang) },
    { label: t(lang, "targetCropLabel"), value: report.crop ?? t(lang, "genericCrop") },
    {
      label: t(lang, "previousCrop"),
      value: plot?.previous_crop ?? "—",
    },
    {
      label: t(lang, "linkFarmer"),
      value: farmer?.phone ?? "—",
    },
  ];

  return (
    <article className="mx-auto w-full max-w-3xl px-6 pb-24 pt-6 text-stone-800">
      {/* Title */}
      <header className="flex flex-wrap items-start justify-between gap-3 border-b-2 border-stone-800 pb-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-stone-900">
            {t(lang, "printReport")} — {report.plot}
          </h1>
          <p className="mt-1 text-sm text-stone-500">
            Soil Fertility Analyzer · {t(lang, "linkFarmer")}: {farmer?.name ?? "—"}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${fertilityChip(report.fertility_status)}`}
          >
            {t(lang, report.fertility_status.toLowerCase())}
          </span>
          {simulated && (
            <span className="rounded-full border border-stone-300 px-2.5 py-0.5 text-[11px] font-medium text-stone-500">
              {t(lang, "iotSimulatedBadge")}
            </span>
          )}
        </div>
      </header>

      {/* Identity */}
      <section className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
        {identity.map((row) => (
          <div key={row.label}>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-stone-400">
              {row.label}
            </p>
            <p className="mt-0.5 font-medium text-stone-800">{row.value}</p>
          </div>
        ))}
      </section>

      {/* Sample results */}
      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold text-stone-900">
          {t(lang, "sampleTitle")}
        </h2>
        <table className="mt-3 w-full border-collapse text-sm">
          <thead>
            <tr className="bg-stone-100 text-left text-xs uppercase tracking-wide text-stone-500">
              <th className="border border-stone-300 px-3 py-2">{t(lang, "element")}</th>
              <th className="border border-stone-300 px-3 py-2">{t(lang, "current")}</th>
              <th className="border border-stone-300 px-3 py-2">{t(lang, "idealRange")}</th>
              <th className="border border-stone-300 px-3 py-2">{t(lang, "fertility")}</th>
              <th className="border border-stone-300 px-3 py-2">{t(lang, "deficit")}</th>
            </tr>
          </thead>
          <tbody>
            {recs.map((rec) => (
              <tr key={rec.element}>
                <td className="border border-stone-300 px-3 py-2 font-medium text-stone-800">
                  {t(lang, ELEMENT_KEY[rec.element] ?? "nitrogen")}
                </td>
                <td className="border border-stone-300 px-3 py-2 tabular-nums">
                  {typeof rec.value === "number"
                    ? `${rec.value.toFixed(1)}${
                        rec.element === "pH" ? "" : ` ${t(lang, "kgPerHa")}`
                      }`
                    : "—"}
                </td>
                <td className="border border-stone-300 px-3 py-2 tabular-nums">
                  {rec.ideal_range || "—"}
                </td>
                <td className="border border-stone-300 px-3 py-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${statusChip(rec.status)}`}
                  >
                    {t(lang, rec.status)}
                  </span>
                </td>
                <td className="border border-stone-300 px-3 py-2 tabular-nums">
                  {rec.deficit > 0 ? rec.deficit.toFixed(1) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Action plan */}
      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold text-stone-900">
          {t(lang, "actionPlan")}
        </h2>
        <ul className="mt-3 space-y-3 text-sm leading-relaxed">
          {recs.map((rec) => (
            <li key={rec.element} className="border-l-2 border-forest pl-3">
              <p className="font-semibold text-stone-800">
                {t(lang, ELEMENT_KEY[rec.element] ?? "nitrogen")} —{" "}
                {t(lang, rec.status)}
              </p>
              <p className="mt-0.5 text-stone-600">{rec.solution}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* Top crops */}
      {(report.crops?.length ?? 0) > 0 && (
        <section className="mt-8">
          <h2 className="font-display text-lg font-semibold text-stone-900">
            {t(lang, "cropsTitle")}
          </h2>
          <ul className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            {report.crops!.map((crop) => (
              <li
                key={crop.crop}
                className="flex items-baseline justify-between gap-2 border border-stone-200 rounded-lg px-3 py-2"
              >
                <span className="font-medium text-stone-800">
                  {crop.crop}
                  {crop.scientific_name && (
                    <span className="ml-1.5 text-xs italic text-stone-400">
                      {crop.scientific_name}
                    </span>
                  )}
                </span>
                <span className="text-xs tabular-nums text-stone-500">
                  {crop.confidence <= 1
                    ? `${(crop.confidence * 100).toFixed(1)}%`
                    : `${crop.confidence}%`}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Fertilizer cost estimate */}
      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold text-stone-900">
          {t(lang, "costEstimate")}
        </h2>
        <table className="mt-3 w-full border-collapse text-sm">
          <thead>
            <tr className="bg-stone-100 text-left text-xs uppercase tracking-wide text-stone-500">
              <th className="border border-stone-300 px-3 py-2">{t(lang, "costProduct")}</th>
              <th className="border border-stone-300 px-3 py-2">{t(lang, "costAmount")}</th>
              <th className="border border-stone-300 px-3 py-2">{t(lang, "costPrice")}</th>
              <th className="border border-stone-300 px-3 py-2 text-right">
                {t(lang, "costSubtotal")}
              </th>
            </tr>
          </thead>
          <tbody>
            {cost.lines.map((line) => (
              <tr key={line.col}>
                <td className="border border-stone-300 px-3 py-2">
                  <span className="font-medium text-stone-800">
                    {t(lang, ELEMENT_KEY[line.col] ?? "nitrogen")}
                  </span>
                  <span className="block text-xs text-stone-500">
                    {line.product
                      ? t(lang, `fert_${line.product}`)
                      : t(lang, "noPurchase")}
                  </span>
                </td>
                <td className="border border-stone-300 px-3 py-2 tabular-nums">
                  {line.purchase ? `${line.amount.toFixed(1)} ${line.unit}` : "—"}
                </td>
                <td className="border border-stone-300 px-3 py-2 tabular-nums">
                  {line.purchase
                    ? `${line.price_etb_per_kg.toFixed(0)} ${t(lang, "etbPerKg")}`
                    : "—"}
                </td>
                <td className="border border-stone-300 px-3 py-2 text-right tabular-nums font-medium">
                  {line.purchase
                    ? `${line.subtotal.toFixed(2)} ${cost.currency}`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-stone-50">
              <td
                colSpan={3}
                className="border border-stone-300 px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-stone-500"
              >
                {t(lang, "totalPerHa")}
              </td>
              <td className="border border-stone-300 px-3 py-2 text-right font-semibold tabular-nums text-stone-900">
                {cost.total_per_ha.toFixed(2)} {cost.currency}
              </td>
            </tr>
            {cost.total_for_plot != null && (
              <tr className="bg-stone-50">
                <td
                  colSpan={3}
                  className="border border-stone-300 px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-stone-500"
                >
                  {t(lang, "totalForPlot")} ({cost.size_ha} ha)
                </td>
                <td className="border border-stone-300 px-3 py-2 text-right font-semibold tabular-nums text-stone-900">
                  {cost.total_for_plot.toFixed(2)} {cost.currency}
                </td>
              </tr>
            )}
          </tfoot>
        </table>
        <p className="mt-2 text-xs text-stone-500">{cost.disclaimer}</p>
      </section>

      {/* Outcome follow-up */}
      {outcome && (
        <section className="mt-8">
          <h2 className="font-display text-lg font-semibold text-stone-900">
            {t(lang, "outcomeTitle")}
          </h2>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            {outcome.implemented_at && (
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-stone-400">
                  {t(lang, "implementedDate")}
                </dt>
                <dd className="mt-0.5 font-medium">
                  {formatDate(outcome.implemented_at, lang)}
                </dd>
              </div>
            )}
            {outcome.expected_yield != null && (
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-stone-400">
                  {t(lang, "expectedYield")}
                </dt>
                <dd className="mt-0.5 font-medium">
                  {outcome.expected_yield} {outcome.yield_unit}
                </dd>
              </div>
            )}
            {outcome.actual_yield != null && (
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-stone-400">
                  {t(lang, "actualYield")}
                </dt>
                <dd className="mt-0.5 font-medium">
                  {outcome.actual_yield} {outcome.yield_unit}
                </dd>
              </div>
            )}
            {outcome.harvest_at && (
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-stone-400">
                  {t(lang, "harvestDate")}
                </dt>
                <dd className="mt-0.5 font-medium">
                  {formatDate(outcome.harvest_at, lang)}
                </dd>
              </div>
            )}
          </dl>
          {outcome.implementation_notes && (
            <p className="mt-2 text-sm text-stone-600">
              <span className="font-semibold text-stone-700">
                {t(lang, "outcomeNotes")}:
              </span>{" "}
              {outcome.implementation_notes}
            </p>
          )}
        </section>
      )}

      {/* Footer */}
      <footer className="mt-10 border-t border-stone-300 pt-4 text-xs leading-relaxed text-stone-500">
        <p>{t(lang, "disclaimer")}</p>
        <p className="mt-1">
          {t(lang, "printReport")} · {t(lang, "sampleDate")}:{" "}
          {formatDate(report.created_at, lang)}
        </p>
      </footer>
    </article>
  );
}
