"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, FlaskConical, Loader2, MapPin, Printer } from "lucide-react";
import OutcomeForm from "@/components/OutcomeForm";
import { dateLocale, t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type { FarmerDetail, Report } from "@/lib/api";
import { fetchFarmer } from "@/lib/api";

function formatDate(iso: string | null | undefined, lang: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(dateLocale(lang));
}

function statusChip(status: string): string {
  if (status === "Good") return "bg-good/10 text-good";
  if (status === "Moderate") return "bg-gold/10 text-soil";
  return "bg-bad/10 text-bad";
}

export default function FarmerProfilePage({ params }: PageProps<"/agent/[id]">) {
  const { id } = use(params);
  const farmerId = Number(id);
  const { lang } = useLang();
  const [detail, setDetail] = useState<FarmerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openOutcomeId, setOpenOutcomeId] = useState<number | null>(null);

  const invalidId = !Number.isFinite(farmerId);

  const load = useCallback(() => {
    fetchFarmer(farmerId)
      .then((data) => {
        setDetail(data);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [farmerId]);

  useEffect(() => {
    if (!invalidId) load();
  }, [load, invalidId]);

  if (invalidId) {
    return (
      <main className="mx-auto w-full max-w-4xl px-6 pb-20 pt-8">
        <Link
          href="/agent"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-stone-500 transition hover:text-forest"
        >
          <ArrowLeft size={16} /> {t(lang, "backToAgent")}
        </Link>
        <div className="mt-6 rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
          <p className="font-semibold">{t(lang, "agentError")}</p>
          <p className="mt-1 opacity-90">{id}</p>
        </div>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="mx-auto w-full max-w-4xl px-6 pb-20 pt-8">
        <p className="inline-flex items-center gap-2 text-sm text-stone-500">
          <Loader2 size={16} className="animate-spin" /> {t(lang, "loading")}
        </p>
      </main>
    );
  }

  if (error || !detail) {
    return (
      <main className="mx-auto w-full max-w-4xl px-6 pb-20 pt-8">
        <Link
          href="/agent"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-stone-500 transition hover:text-forest"
        >
          <ArrowLeft size={16} /> {t(lang, "backToAgent")}
        </Link>
        <div className="mt-6 rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
          <p className="font-semibold">{t(lang, "agentError")}</p>
          {error && <p className="mt-1 opacity-90">{error}</p>}
        </div>
      </main>
    );
  }

  const { farmer, plots, reports } = detail;

  return (
    <main className="mx-auto w-full max-w-4xl px-6 pb-20 pt-8">
      <Link
        href="/agent"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-stone-500 transition hover:text-forest"
      >
        <ArrowLeft size={16} /> {t(lang, "backToAgent")}
      </Link>

      <div className="mt-4 flex items-start gap-4">
        <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-forest/10 font-display text-2xl font-semibold text-forest">
          {farmer.name.charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0">
          <h1 className="font-display text-3xl font-semibold text-stone-900">{farmer.name}</h1>
          <p className="mt-1 text-sm text-stone-500">
            {[farmer.region, farmer.phone].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
      </div>

      {/* Plots */}
      <section className="mt-8">
        <h2 className="font-display text-xl font-semibold text-stone-900">{t(lang, "plots")}</h2>
        {plots.length === 0 ? (
          <p className="mt-3 rounded-2xl border border-dashed border-stone-300 px-5 py-6 text-center text-sm text-stone-400">
            {t(lang, "noPlots")}
          </p>
        ) : (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {plots.map((plot) => (
              <div
                key={plot.id}
                className="rounded-2xl bg-white p-4 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-semibold text-stone-800">{plot.name}</p>
                  {plot.latest_status && (
                    <span
                      className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${statusChip(plot.latest_status)}`}
                    >
                      {t(lang, plot.latest_status.toLowerCase())}
                    </span>
                  )}
                </div>
                <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-stone-400">
                  {plot.farm_size_ha != null && <span>{plot.farm_size_ha} ha</span>}
                  {plot.previous_crop && <span>{plot.previous_crop}</span>}
                  <span>
                    {plot.report_count ?? 0} {t(lang, "reportCount")}
                  </span>
                  {plot.latitude != null && plot.longitude != null && (
                    <span className="inline-flex items-center gap-1">
                      <MapPin size={12} /> {plot.latitude.toFixed(2)}, {plot.longitude.toFixed(2)}
                    </span>
                  )}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Soil test history */}
      <section className="mt-8">
        <h2 className="font-display text-xl font-semibold text-stone-900">{t(lang, "reports")}</h2>
        {reports.length === 0 ? (
          <p className="mt-3 rounded-2xl border border-dashed border-stone-300 px-5 py-6 text-center text-sm text-stone-400">
            {t(lang, "noReports")}
          </p>
        ) : (
          <div className="mt-3 space-y-3">
            {reports.map((report: Report) => (
              <div
                key={report.id}
                className="rounded-2xl bg-white p-4 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]"
              >
                <div className="flex flex-wrap items-center gap-3">
                  <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-stone-800">
                    <FlaskConical size={15} className="text-forest" />
                    {report.plot}
                  </span>
                  <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${statusChip(report.fertility_status)}`}>
                    {t(lang, report.fertility_status.toLowerCase())}
                  </span>
                  <span className="text-xs text-stone-400">
                    {formatDate(report.created_at, lang)}
                  </span>
                  <span className="ml-auto text-xs tabular-nums text-stone-500">
                    N {report.N} · P {report.P} · K {report.K} · pH {report.ph}
                  </span>
                  <Link
                    href={`/print/${report.id}`}
                    className="inline-flex items-center gap-1 text-xs font-medium text-forest transition hover:brightness-110 print:hidden"
                  >
                    <Printer size={13} /> {t(lang, "printBtn")}
                  </Link>
                </div>

                {report.outcome ? (
                  <div className="mt-3 rounded-xl border border-stone-200 bg-stone-50 px-4 py-3 text-xs text-stone-600">
                    <p className="font-semibold text-stone-700">{t(lang, "outcomeTitle")}</p>
                    <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                      {report.outcome.implemented_at && (
                        <span>
                          {t(lang, "implementedDate")}: {formatDate(report.outcome.implemented_at, lang)}
                        </span>
                      )}
                      {report.outcome.expected_yield != null && (
                        <span>
                          {t(lang, "expectedYield")}: {report.outcome.expected_yield} {report.outcome.yield_unit}
                        </span>
                      )}
                      {report.outcome.actual_yield != null && (
                        <span>
                          {t(lang, "actualYield")}: {report.outcome.actual_yield} {report.outcome.yield_unit}
                        </span>
                      )}
                      {report.outcome.harvest_at && (
                        <span>
                          {t(lang, "harvestDate")}: {formatDate(report.outcome.harvest_at, lang)}
                        </span>
                      )}
                    </p>
                    {report.outcome.implementation_notes && (
                      <p className="mt-1 text-stone-500">
                        {report.outcome.implementation_notes}
                      </p>
                    )}
                  </div>
                ) : openOutcomeId === report.id ? (
                  <OutcomeForm
                    reportId={report.id}
                    onSaved={() => {
                      setOpenOutcomeId(null);
                      load();
                    }}
                  />
                ) : (
                  <button
                    onClick={() => setOpenOutcomeId(report.id)}
                    className="mt-3 rounded-xl border border-stone-200 px-4 py-2 text-xs font-semibold text-stone-600 transition hover:border-forest hover:text-forest"
                  >
                    {t(lang, "recordOutcome")}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
