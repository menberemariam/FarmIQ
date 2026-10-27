"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, Loader2, UserPlus } from "lucide-react";
import { dateLocale, t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type { Farmer } from "@/lib/api";
import { createFarmer, fetchFarmers } from "@/lib/api";

function formatDate(iso: string | null | undefined, lang: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(dateLocale(lang));
}

export default function AgentPage() {
  const { lang } = useLang();
  const [farmers, setFarmers] = useState<Farmer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [region, setRegion] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchFarmers()
      .then((data) => {
        setFarmers(data.farmers);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const register = useCallback(async () => {
    setFormError(null);
    if (!name.trim()) {
      setFormError(t(lang, "required"));
      return;
    }
    setCreating(true);
    try {
      await createFarmer({
        name: name.trim(),
        phone: phone.trim() || null,
        region: region.trim() || null,
        lang,
      });
      setName("");
      setPhone("");
      setRegion("");
      setShowForm(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }, [name, phone, region, lang, load]);

  return (
    <main className="mx-auto w-full max-w-4xl px-6 pb-20 pt-8">
      <h1 className="font-display text-3xl font-semibold text-stone-900">
        {t(lang, "agentTitle")}
      </h1>
      <p className="mt-2 text-sm text-stone-500">{t(lang, "agentSubtitle")}</p>

      <div className="mt-6 flex items-center justify-between">
        <h2 className="font-display text-xl font-semibold text-stone-900">
          {t(lang, "farmers")}
        </h2>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="inline-flex items-center gap-2 rounded-2xl bg-forest px-4 py-2.5 text-sm font-semibold text-white shadow transition hover:brightness-110"
        >
          <UserPlus size={16} />
          {t(lang, "registerFarmer")}
        </button>
      </div>

      {showForm && (
        <div className="mt-4 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="block sm:col-span-1">
              <span className="text-xs font-medium text-stone-500">{t(lang, "farmerName")} *</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest"
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-stone-500">{t(lang, "phone")}</span>
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest"
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-stone-500">{t(lang, "region")}</span>
              <input
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest"
              />
            </label>
          </div>
          {formError && (
            <p className="mt-3 rounded-xl border border-bad/30 bg-bad/10 px-4 py-2.5 text-sm text-bad">
              {formError}
            </p>
          )}
          <div className="mt-4 flex gap-3">
            <button
              onClick={register}
              disabled={creating}
              className="inline-flex items-center gap-2 rounded-xl bg-forest px-5 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-70"
            >
              {creating && <Loader2 size={16} className="animate-spin" />}
              {t(lang, "registerFarmer")}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="rounded-xl border border-stone-200 px-5 py-2.5 text-sm font-medium text-stone-600 transition hover:bg-stone-50"
            >
              {t(lang, "cancel")}
            </button>
          </div>
        </div>
      )}

      {loading && (
        <p className="mt-8 inline-flex items-center gap-2 text-sm text-stone-500">
          <Loader2 size={16} className="animate-spin" /> {t(lang, "loading")}
        </p>
      )}

      {error && (
        <div className="mt-6 rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
          <p className="font-semibold">{t(lang, "agentError")}</p>
          <p className="mt-1 opacity-90">{error}</p>
        </div>
      )}

      {!loading && !error && farmers.length === 0 && (
        <p className="mt-8 rounded-2xl border border-dashed border-stone-300 px-5 py-8 text-center text-sm text-stone-400">
          {t(lang, "noFarmers")}
        </p>
      )}

      <div className="mt-5 space-y-3">
        {farmers.map((farmer) => (
          <Link
            key={farmer.id}
            href={`/agent/${farmer.id}`}
            className="animate-fade-up flex items-center gap-4 rounded-2xl bg-white p-4 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)] transition hover:border-forest/40 hover:shadow-md"
          >
            <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-forest/10 font-display text-lg font-semibold text-forest">
              {farmer.name.charAt(0).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-stone-800">{farmer.name}</p>
              <p className="truncate text-xs text-stone-400">
                {[farmer.region, farmer.phone].filter(Boolean).join(" · ") || "—"}
              </p>
            </div>
            <div className="hidden gap-6 text-center sm:flex">
              <div>
                <p className="text-sm font-semibold tabular-nums text-stone-800">
                  {farmer.plot_count ?? 0}
                </p>
                <p className="text-[11px] text-stone-400">{t(lang, "plotCount")}</p>
              </div>
              <div>
                <p className="text-sm font-semibold tabular-nums text-stone-800">
                  {farmer.report_count ?? 0}
                </p>
                <p className="text-[11px] text-stone-400">{t(lang, "reportCount")}</p>
              </div>
              <div>
                <p className="text-sm font-medium text-stone-600">
                  {formatDate(farmer.last_report_at, lang)}
                </p>
                <p className="text-[11px] text-stone-400">{t(lang, "lastTest")}</p>
              </div>
            </div>
            <ChevronRight size={18} className="shrink-0 text-stone-300" />
          </Link>
        ))}
      </div>
    </main>
  );
}
