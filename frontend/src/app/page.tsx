"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, FlaskConical, Leaf, Loader2, Sprout } from "lucide-react";

import ActionPlan from "@/components/ActionPlan";
import ClimateAccordion from "@/components/ClimateAccordion";
import CropMatchCard from "@/components/CropMatchCard";
import FertilityGauge from "@/components/FertilityGauge";
import Hero from "@/components/Hero";
import NutrientCard from "@/components/NutrientCard";
import NutrientSlider from "@/components/NutrientSlider";
import PhSlider from "@/components/PhSlider";
import StepNav from "@/components/StepNav";
import type { Lang } from "@/i18n/strings";
import { LANG_LABELS, t } from "@/i18n/strings";
import {
  type ClimateValues,
  type CropInfo,
  type PredictResponse,
  type SoilValues,
  checkHealth,
  fetchCrops,
  predict,
} from "@/lib/api";

type ApiStatus = "checking" | "online" | "offline";

const INITIAL_SOIL: SoilValues = { N: 40, P: 20, K: 110, ph: 6.5 };
const INITIAL_CLIMATE: ClimateValues = { temperature: 28, humidity: 78, rainfall: 190 };

export default function Page() {
  const [lang, setLang] = useState<Lang>("en");
  const [step, setStep] = useState(1);
  const [values, setValues] = useState<SoilValues>(INITIAL_SOIL);
  const [climate, setClimate] = useState<ClimateValues>(INITIAL_CLIMATE);
  const [crop, setCrop] = useState<string>("generic");
  const [crops, setCrops] = useState<CropInfo[]>([]);
  const [result, setResult] = useState<PredictResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshingCrops, setRefreshingCrops] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    fetchCrops()
      .then((data) => setCrops(data.crops))
      .catch(() => setCrops([]));
  }, []);

  useEffect(() => {
    let alive = true;
    const probe = async () => {
      const ok = await checkHealth();
      if (alive) setApiStatus(ok ? "online" : "offline");
    };
    probe();
    const timer = setInterval(() => {
      setApiStatus((status) => {
        if (status === "online") return status;
        probe();
        return status;
      });
    }, 10_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const runPredict = useCallback(
    async (soil: SoilValues, climateValues: ClimateValues) => {
      setError(null);
      setLoading(true);
      try {
        const response = await predict(
          soil,
          crop === "generic" ? null : crop,
          climateValues,
          lang,
        );
        setResult(response);
        setStep(2);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setStep(1);
      } finally {
        setLoading(false);
      }
    },
    [crop],
  );

  const refreshCrops = useCallback(async () => {
    setError(null);
    setRefreshingCrops(true);
    try {
      const response = await predict(values, crop === "generic" ? null : crop, climate, lang);
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshingCrops(false);
    }
  }, [values, climate, crop]);

  const retryConnection = useCallback(async () => {
    setError(null);
    setApiStatus("checking");
    const ok = await checkHealth();
    setApiStatus(ok ? "online" : "offline");
    try {
      const data = await fetchCrops();
      setCrops(data.crops);
    } catch {
      /* crops will retry with the next attempt */
    }
    if (result) {
      await refreshCrops();
    }
  }, [result, refreshCrops]);

  const updateValue = (key: keyof SoilValues) => (value: number) =>
    setValues((v) => ({ ...v, [key]: value }));

  const selectedCrop = crop !== "generic" ? crops.find((c) => c.name === crop) : null;

  const apiPill = (() => {
    if (apiStatus === "checking") {
      return { dot: "bg-stone-400 animate-pulse", text: t(lang, "apiChecking"), cls: "bg-stone-100 text-stone-500" };
    }
    if (apiStatus === "online") {
      return { dot: "bg-good", text: t(lang, "apiOnline"), cls: "bg-good/10 text-good" };
    }
    return { dot: "bg-bad", text: t(lang, "apiOffline"), cls: "bg-bad/10 text-bad" };
  })();

  return (
    <main>
      {/* language switcher + api status */}
      <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-end gap-2 px-6 pt-4">
        <span
          className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium ${apiPill.cls}`}
          title={t(lang, "apiOffline")}
        >
          <span className={`size-2 shrink-0 rounded-full ${apiPill.dot}`} />
          <span className="truncate">{apiPill.text}</span>
        </span>
        <span className="text-xs font-medium text-stone-500">{t(lang, "language")}</span>
        <select
          value={lang}
          onChange={(e) => setLang(e.target.value as Lang)}
          className="rounded-xl border border-stone-200 bg-white px-3 py-1.5 text-sm font-medium text-stone-800 outline-none focus:border-forest"
        >
          {(Object.keys(LANG_LABELS) as Lang[]).map((l) => (
            <option key={l} value={l}>
              {LANG_LABELS[l]}
            </option>
          ))}
        </select>
      </div>

      {step === 1 && (
        <>
          <Hero lang={lang} onStart={() => document.getElementById("analyzer")?.scrollIntoView({ behavior: "smooth" })} />
          <StepNav step={1} lang={lang} />

          <section id="analyzer" className="mx-auto max-w-4xl px-6 pb-20">
            {error && (
              <div className="mb-5 rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
                <p className="font-semibold">{t(lang, "error")}</p>
                <p className="mt-1 opacity-90">{error}</p>
                <button
                  onClick={retryConnection}
                  className="mt-3 rounded-xl bg-bad px-4 py-2 text-xs font-semibold text-white transition hover:brightness-110"
                >
                  {t(lang, "retry")}
                </button>
              </div>
            )}

            <div className="mb-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
              <label className="text-sm font-semibold text-stone-800">
                {t(lang, "targetCrop")}
              </label>
              <select
                value={crop}
                onChange={(e) => setCrop(e.target.value)}
                className="mt-2 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
              >
                <option value="generic">{t(lang, "genericCrop")}</option>
                {crops.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name.charAt(0).toUpperCase() + c.name.slice(1)}
                  </option>
                ))}
              </select>
              {selectedCrop && (
                <p className="mt-2 text-xs text-stone-400">
                  {selectedCrop.scientific_name} · N {selectedCrop.bands.N[0]}–{selectedCrop.bands.N[1]} · P {selectedCrop.bands.P[0]}–{selectedCrop.bands.P[1]} · K {selectedCrop.bands.K[0]}–{selectedCrop.bands.K[1]} · pH {selectedCrop.bands.ph[0]}–{selectedCrop.bands.ph[1]}
                </p>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <NutrientSlider
                label={t(lang, "nitrogen")}
                unit={t(lang, "kgPerHa")}
                min={0}
                max={160}
                value={values.N}
                onChange={updateValue("N")}
                accent="#14532d"
                icon={<Leaf size={20} />}
              />
              <NutrientSlider
                label={t(lang, "phosphorus")}
                unit={t(lang, "kgPerHa")}
                min={0}
                max={160}
                value={values.P}
                onChange={updateValue("P")}
                accent="#92400e"
                icon={<FlaskConical size={20} />}
              />
              <NutrientSlider
                label={t(lang, "potassium")}
                unit={t(lang, "kgPerHa")}
                min={0}
                max={220}
                value={values.K}
                onChange={updateValue("K")}
                accent="#d97706"
                icon={<Sprout size={20} />}
              />
              <PhSlider value={values.ph} onChange={updateValue("ph")} lang={lang} />
            </div>

            <button
              onClick={() => runPredict(values, climate)}
              disabled={loading}
              className="mt-7 flex w-full items-center justify-center gap-2 rounded-2xl bg-forest py-4 text-lg font-semibold text-white shadow-lg transition hover:brightness-110 active:scale-[0.99] disabled:opacity-70"
            >
              {loading ? (
                <>
                  <Loader2 size={20} className="animate-spin" />
                  {t(lang, "predicting")}
                </>
              ) : (
                t(lang, "predict")
              )}
            </button>
          </section>
        </>
      )}

      {step === 2 && result && (
        <div className="mx-auto max-w-4xl px-6 pb-20">
          <button
            onClick={() => setStep(1)}
            className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-stone-500 transition hover:text-forest"
          >
            <ArrowLeft size={16} /> {t(lang, "analyzeAgain")}
          </button>
          <StepNav step={2} lang={lang} />

          {error && (
            <div className="mb-5 rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
              <p className="font-semibold">{t(lang, "error")}</p>
              <p className="mt-1 opacity-90">{error}</p>
              <button
                onClick={retryConnection}
                className="mt-3 rounded-xl bg-bad px-4 py-2 text-xs font-semibold text-white transition hover:brightness-110"
              >
                {t(lang, "retry")}
              </button>
            </div>
          )}

          <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
            <FertilityGauge
              status={result.fertility_status}
              confidence={result.confidence}
              statusLabel={t(lang, result.fertility_status.toLowerCase())}
              confidenceLabel={t(lang, "confidence")}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              {result.recommendations.map((rec, i) => (
                <NutrientCard
                  key={rec.element}
                  rec={rec}
                  index={i}
                  idealLabel={t(lang, "idealRange")}
                  currentLabel={t(lang, "current")}
                />
              ))}
            </div>
          </div>

          <div className="mt-6">
            <ActionPlan recommendations={result.recommendations} lang={lang} />
          </div>

          <div className="mt-10 flex items-center justify-between">
            <h3 className="font-display text-2xl font-semibold text-stone-900">
              {t(lang, "recommendation")} · {t(lang, "step3")}
            </h3>
            <button
              onClick={() => setStep(3)}
              className="rounded-2xl bg-forest px-6 py-3 text-sm font-semibold text-white shadow transition hover:brightness-110"
            >
              {t(lang, "step3")} →
            </button>
          </div>
        </div>
      )}

      {step === 3 && result && (
        <div className="mx-auto max-w-4xl px-6 pb-20">
          <button
            onClick={() => setStep(2)}
            className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-stone-500 transition hover:text-forest"
          >
            <ArrowLeft size={16} /> {t(lang, "analyzeAgain")}
          </button>
          <StepNav step={3} lang={lang} />

          {error && (
            <div className="mb-5 rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
              <p className="font-semibold">{t(lang, "error")}</p>
              <p className="mt-1 opacity-90">{error}</p>
              <button
                onClick={retryConnection}
                className="mt-3 rounded-xl bg-bad px-4 py-2 text-xs font-semibold text-white transition hover:brightness-110"
              >
                {t(lang, "retry")}
              </button>
            </div>
          )}

          {result.crops[0] && (
            <CropMatchCard
              match={result.crops[0]}
              lang={lang}
              confidenceLabel={t(lang, "confidence")}
              tempLabel={t(lang, "temp")}
              rainfallLabel={t(lang, "rainfall")}
            />
          )}

          {result.crops.length > 1 && (
            <div className="mt-6">
              <h3 className="font-display mb-4 text-lg font-semibold text-stone-900">
                {t(lang, "otherMatches")}
              </h3>
              <div className="space-y-3">
                {result.crops.slice(1).map((match, i) => (
                  <div
                    key={match.crop}
                    className="animate-fade-up flex items-center gap-4 rounded-2xl bg-white p-4 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]"
                    style={{ animationDelay: `${i * 20}ms` }}
                  >
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-forest/10 font-display text-lg font-semibold text-forest">
                      {i + 2}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold capitalize text-stone-800">{match.crop}</p>
                      <p className="truncate text-xs italic text-stone-400">
                        {match.scientific_name}
                      </p>
                    </div>
                    <div className="w-32">
                      <div className="mb-1 flex justify-between text-[11px] text-stone-400">
                        <span>{t(lang, "confidence")}</span>
                        <span className="tabular-nums text-stone-600">{match.confidence.toFixed(0)}%</span>
                      </div>
                      <div className="h-1.5 w-full rounded-full bg-stone-100">
                        <div
                          className="h-1.5 rounded-full bg-gold"
                          style={{ width: `${match.confidence}%` }}
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-8">
            <ClimateAccordion
              climate={climate}
              onChange={setClimate}
              lang={lang}
              onRefresh={refreshCrops}
              refreshing={refreshingCrops}
            />
          </div>
        </div>
      )}

      <footer className="mx-auto w-full max-w-4xl px-6 pb-10 text-center">
        <p className="text-xs text-stone-400">{t(lang, "disclaimer")}</p>
        <p className="mt-2 text-xs font-medium text-stone-500">
          🌱 {t(lang, "poweredBy")}
        </p>
      </footer>
    </main>
  );
}
