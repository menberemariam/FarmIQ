"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, Printer } from "lucide-react";
import PrintableReport from "@/components/PrintableReport";
import { t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type { CostEstimate, ReportDetail } from "@/lib/api";
import { fetchCostEstimate, fetchReportDetail } from "@/lib/api";

export default function PrintReportPage({
  params,
}: PageProps<"/print/[reportId]">) {
  const { reportId } = use(params);
  const reportIdNumber = Number(reportId);
  const { lang } = useLang();
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [cost, setCost] = useState<CostEstimate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const invalidId = !Number.isFinite(reportIdNumber);

  useEffect(() => {
    if (invalidId) return;
    Promise.all([
      fetchReportDetail(reportIdNumber),
      fetchCostEstimate(reportIdNumber),
    ])
      .then(([reportDetail, costEstimate]) => {
        setDetail(reportDetail);
        setCost(costEstimate);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [invalidId, reportIdNumber]);

  if (invalidId) {
    return (
      <main className="mx-auto w-full max-w-3xl px-6 pb-16 pt-6">
        <div className="rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
          <p className="font-semibold">{t(lang, "agentError")}</p>
          <p className="mt-1 opacity-90">{reportId}</p>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-6 pb-16 pt-6">
      {/* Toolbar — hidden on paper */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 print:hidden">
        <Link
          href={detail?.farmer ? `/agent/${detail.farmer.id}` : "/agent"}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-stone-500 transition hover:text-forest"
        >
          <ArrowLeft size={16} /> {t(lang, "printBack")}
        </Link>
        <button
          type="button"
          onClick={() => window.print()}
          className="inline-flex items-center gap-2 rounded-xl bg-forest px-4 py-2 text-sm font-semibold text-white transition hover:brightness-110"
        >
          <Printer size={16} /> {t(lang, "printBtn")}
        </button>
      </div>

      {loading && (
        <p className="inline-flex items-center gap-2 text-sm text-stone-500">
          <Loader2 size={16} className="animate-spin" /> {t(lang, "loading")}
        </p>
      )}

      {!loading && error && (
        <div className="rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
          <p className="font-semibold">{t(lang, "agentError")}</p>
          {error && <p className="mt-1 opacity-90">{error}</p>}
        </div>
      )}

      {!loading && !error && detail && cost && (
        <PrintableReport detail={detail} cost={cost} lang={lang} />
      )}
    </main>
  );
}
