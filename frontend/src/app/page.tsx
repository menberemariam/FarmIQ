"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, FlaskConical, Leaf, Loader2, Sprout } from "lucide-react";

import ActionPlan from "@/components/ActionPlan";
import ClimateAccordion from "@/components/ClimateAccordion";
import CropMatchCard from "@/components/CropMatchCard";
import FertilityGauge from "@/components/FertilityGauge";
import Hero from "@/components/Hero";
import NutrientCard from "@/components/NutrientCard";
import NutrientSlider from "@/components/NutrientSlider";
import PhSlider from "@/components/PhSlider";
import SavePanel from "@/components/SavePanel";
import StepNav from "@/components/StepNav";
import SuitabilityChip from "@/components/SuitabilityChip";
import { t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import {
  cmolcKToKgHa,
  kgHaToCmolcK,
  kgHaToPctN,
  pctNToKgHa,
} from "@/lib/units";
import {
  type ClimateValues,
  type CropInfo,
  type EthiopianCrop,
  type LimeType,
  type PredictResponse,
  type SeasonEntry,
  type SoilConditioningInput,
  type SoilConditioningReference,
  type SoilValues,
  type SuitabilityResult,
  type VarietyEntry,
  buildPredictPayload,
  checkHealth,
  fetchCrops,
  fetchEthiopianCrops,
  fetchSeasons,
  fetchSoilConditioning,
  fetchSuitability,
  fetchVarieties,
  predict,
  predictStream,
} from "@/lib/api";

type ApiStatus = "checking" | "online" | "offline";
type InputUnits = "kg_ha" | "lab";

const CLASS_ORDER: Record<string, number> = { S1: 0, S2: 1, S3: 2, N: 3 };

const INITIAL_SOIL: SoilValues = { N: 40, P: 20, K: 110, ph: 6.5 };
const INITIAL_CLIMATE: ClimateValues = { temperature: 28, humidity: 78, rainfall: 190 };

// Opt-in: the streaming endpoint is additive, and the classic predict()
// path stays the default until a deployment explicitly enables SSE.
const LLM_STREAMING_ENABLED =
  process.env.NEXT_PUBLIC_LLM_STREAMING === "1";

// Ethiopian agroecological zones (Dataset/agroecological_zones.json). The
// "" entry keeps the legacy generic/EthioSIS behavior.
const ZONE_OPTIONS: { id: string; labelKey: string }[] = [
  { id: "highland_nitisols", labelKey: "zoneNitisols" },
  { id: "highland_vertisols", labelKey: "zoneVertisols" },
  { id: "rift_valley", labelKey: "zoneRift" },
  { id: "arid_semi_arid", labelKey: "zoneArid" },
];

// Ethiopian soil types for the LRF lime requirement (Dataset/lime_requirement.json).
// The "" entry keeps the legacy generic lime formula.
const SOIL_TYPE_OPTIONS: { id: string; labelKey: string }[] = [
  { id: "nitisol", labelKey: "soilNitisol" },
  { id: "vertisol", labelKey: "soilVertisol" },
  { id: "cambisol_luvisol", labelKey: "soilCambisolLuvisol" },
  { id: "andosol", labelKey: "soilAndosol" },
  { id: "arenosol", labelKey: "soilArenosol" },
];

const TEXTURE_LABEL_KEYS: Record<string, string> = {
  sandy: "textureSandy",
  loam: "textureLoam",
  clay: "textureClay",
};

const SOC_LABEL_KEYS: Record<string, string> = {
  very_low: "socVeryLow",
  low: "socLow",
  medium: "socMedium",
  high: "socHigh",
};

export default function Page() {
  const { lang } = useLang();
  const [step, setStep] = useState(1);
  const [values, setValues] = useState<SoilValues>(INITIAL_SOIL);
  const [climate, setClimate] = useState<ClimateValues>(INITIAL_CLIMATE);
  const [inputUnits, setInputUnits] = useState<InputUnits>("kg_ha");
  const [crop, setCrop] = useState<string>("generic");
  const [variety, setVariety] = useState<string>("");
  const [varieties, setVarieties] = useState<VarietyEntry[]>([]);
  const [seasons, setSeasons] = useState<SeasonEntry[]>([]);
  const [seasonCrops, setSeasonCrops] = useState<string[]>([]);
  const [season, setSeason] = useState<string>("");
  const [zone, setZone] = useState<string>("");
  const [soilType, setSoilType] = useState<string>("");
  const [limeType, setLimeType] = useState<LimeType>("calcitic");
  const [conditioning, setConditioning] = useState<SoilConditioningInput>({});
  const [conditioningRef, setConditioningRef] =
    useState<SoilConditioningReference | null>(null);
  const [crops, setCrops] = useState<CropInfo[]>([]);
  const [result, setResult] = useState<PredictResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const [refreshingCrops, setRefreshingCrops] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [stapleMeta, setStapleMeta] = useState<Record<string, EthiopianCrop>>({});
  const [stapleSuitability, setStapleSuitability] = useState<SuitabilityResult[] | null>(null);

  useEffect(() => {
    fetchEthiopianCrops()
      .then((data) => {
        setStapleMeta(
          Object.fromEntries(
            data.crops.filter((c) => !c.in_ml_model).map((c) => [c.name, c]),
          ),
        );
      })
      .catch(() => setStapleMeta({}));
  }, []);

  useEffect(() => {
    if (step !== 3) return;
    let alive = true;
    fetchSuitability({
      ph: values.ph,
      temperature: climate.temperature,
      rainfall: climate.rainfall,
    })
      .then((data) => {
        if (alive) {
          setStapleSuitability(
            [...data.results].sort(
              (a, b) => (CLASS_ORDER[a.class] ?? 9) - (CLASS_ORDER[b.class] ?? 9),
            ),
          );
        }
      })
      .catch(() => {
        if (alive) setStapleSuitability([]);
      });
    return () => {
      alive = false;
    };
  }, [step, values.ph, climate.temperature, climate.rainfall]);

  useEffect(() => {
    fetchCrops()
      .then((data) => setCrops(data.crops))
      .catch(() => setCrops([]));
  }, []);

  useEffect(() => {
    fetchVarieties()
      .then((data) => setVarieties(data.varieties))
      .catch(() => setVarieties([]));
  }, []);

  useEffect(() => {
    fetchSeasons()
      .then((data) => {
        setSeasons(data.seasons);
        setSeasonCrops(data.crops);
      })
      .catch(() => {
        setSeasons([]);
        setSeasonCrops([]);
      });
  }, []);

  useEffect(() => {
    fetchSoilConditioning()
      .then((data) => setConditioningRef(data))
      .catch(() => setConditioningRef(null));
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

  const selectedVariety =
    varieties.find((entry) => entry.id === variety) ?? null;

  const varietyCrops = Array.from(
    new Set(varieties.map((entry) => entry.crop)),
  ).filter((name) => !crops.some((c) => c.name === name));

  const seasonOnlyCrops = seasonCrops.filter(
    (name) => !crops.some((c) => c.name === name) && !varietyCrops.includes(name),
  );

  const seasonalCrop = crop !== "generic" && seasonCrops.includes(crop);
  const effectiveSeason = seasonalCrop ? season || "meher" : "";
  const selectedSeason =
    seasons.find((entry) => entry.id === effectiveSeason) ?? null;

  const resetSeasonForCrop = (nextCrop: string) => {
    if (nextCrop !== "generic" && seasonCrops.includes(nextCrop)) {
      setSeason((current) => current || "meher");
    } else {
      setSeason("");
    }
  };

  useEffect(() => () => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const runPredict = useCallback(
    async (soil: SoilValues, climateValues: ClimateValues) => {
      setError(null);
      setLoading(true);

      // A new analysis supersedes any in-flight stream.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const cropValue = crop === "generic" ? null : crop;
      const payload = buildPredictPayload(
        soil,
        cropValue,
        climateValues,
        lang,
        true,
        zone || null,
        soilType || null,
        limeType,
        selectedVariety?.variety ?? null,
        selectedVariety?.region ?? null,
        effectiveSeason || null,
        conditioning,
      );

      const runNonStreaming = async () => {
        const response = await predict(
          soil,
          cropValue,
          climateValues,
          lang,
          true,
          zone || null,
          soilType || null,
          limeType,
          selectedVariety?.variety ?? null,
          selectedVariety?.region ?? null,
          effectiveSeason || null,
          conditioning,
        );
        setResult(response);
        setStep(2);
      };

      let snapshotReceived = false;
      let streamingAttempt = false;
      try {
        if (!LLM_STREAMING_ENABLED) {
          await runNonStreaming();
          return;
        }

        streamingAttempt = true;
        setStreaming(true);
        await predictStream(
          payload,
          {
            onSnapshot: (snapshot) => {
              snapshotReceived = true;
              setResult(snapshot);
              setStep(2);
            },
            onElement: (rec) => {
              setResult((prev) =>
                prev
                  ? {
                      ...prev,
                      recommendations: prev.recommendations.map((item) =>
                        item.element === rec.element ? rec : item,
                      ),
                      llm_used: prev.llm_used || rec.source === "llm",
                    }
                  : prev,
              );
            },
            onDone: (done) => {
              setResult((prev) =>
                prev
                  ? {
                      ...prev,
                      recommendations: done.recommendations,
                      llm_used: done.llm_used,
                    }
                  : prev,
              );
            },
          },
          controller.signal,
        );
      } catch (err) {
        if (controller.signal.aborted) return;
        if (!streamingAttempt) {
          setError(err instanceof Error ? err.message : String(err));
          setStep(1);
          return;
        }
        // With a snapshot on screen, keep the validated deterministic result.
        if (snapshotReceived) return;
        try {
          await runNonStreaming();
        } catch (fallbackErr) {
          setError(
            fallbackErr instanceof Error
              ? fallbackErr.message
              : String(fallbackErr),
          );
          setStep(1);
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
          setStreaming(false);
          setLoading(false);
        }
      }
    },
    [crop, lang, zone, soilType, limeType, selectedVariety, effectiveSeason, conditioning],
  );

  const refreshCrops = useCallback(async () => {
    setError(null);
    setRefreshingCrops(true);
    try {
      const response = await predict(
        values,
        crop === "generic" ? null : crop,
        climate,
        lang,
        true,
        zone || null,
        soilType || null,
        limeType,
        selectedVariety?.variety ?? null,
        selectedVariety?.region ?? null,
        effectiveSeason || null,
        conditioning,
      );
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshingCrops(false);
    }
  }, [values, climate, crop, lang, zone, soilType, limeType, selectedVariety, effectiveSeason, conditioning]);

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

  const updateConditioning = (patch: SoilConditioningInput) =>
    setConditioning((current) => ({ ...current, ...patch }));

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
      {/* api status */}
      <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-end gap-2 px-6 pt-4">
        <span
          className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium ${apiPill.cls}`}
          title={t(lang, "apiOffline")}
        >
          <span className={`size-2 shrink-0 rounded-full ${apiPill.dot}`} />
          <span className="truncate">{apiPill.text}</span>
        </span>
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
                onChange={(e) => {
                  setCrop(e.target.value);
                  setVariety("");
                  resetSeasonForCrop(e.target.value);
                }}
                className="mt-2 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
              >
                <option value="generic">{t(lang, "genericCrop")}</option>
                {crops.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name.charAt(0).toUpperCase() + c.name.slice(1)}
                  </option>
                ))}
                {varietyCrops.map((name) => (
                  <option key={name} value={name}>
                    {name.charAt(0).toUpperCase() + name.slice(1)} (Ethiopian variety data)
                  </option>
                ))}
                {seasonOnlyCrops.map((name) => (
                  <option key={name} value={name}>
                    {name.charAt(0).toUpperCase() + name.slice(1)} (Seasonal data)
                  </option>
                ))}
              </select>
              {selectedCrop && (
                <p className="mt-2 text-xs text-stone-400">
                  {selectedCrop.scientific_name} · N {selectedCrop.bands.N[0]}–{selectedCrop.bands.N[1]} · P {selectedCrop.bands.P[0]}–{selectedCrop.bands.P[1]} · K {selectedCrop.bands.K[0]}–{selectedCrop.bands.K[1]} · pH {selectedCrop.bands.ph[0]}–{selectedCrop.bands.ph[1]}
                </p>
              )}
            </div>

            {varieties.length > 0 && (
              <div className="mb-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
                <label className="text-sm font-semibold text-stone-800">
                  {t(lang, "varietyLabel")}
                </label>
                <select
                  value={variety}
                  onChange={(e) => {
                    const next = varieties.find((entry) => entry.id === e.target.value);
                    setVariety(e.target.value);
                    if (next) {
                      setCrop(next.crop);
                      resetSeasonForCrop(next.crop);
                    }
                  }}
                  className="mt-2 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
                >
                  <option value="">{t(lang, "varietyNone")}</option>
                  {varieties.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.crop_label} — {entry.variety} ({entry.region_label})
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-xs text-stone-400">
                  {t(lang, "varietyCaption")}
                </p>
                {selectedVariety && (
                  <p className="mt-2 text-xs text-stone-500">
                    N {selectedVariety.bands.N[0]}–{selectedVariety.bands.N[1]} · P{" "}
                    {selectedVariety.bands.P[0]}–{selectedVariety.bands.P[1]} · K{" "}
                    {selectedVariety.bands.K[0]}–{selectedVariety.bands.K[1]} · pH{" "}
                    {selectedVariety.bands.ph[0]}–{selectedVariety.bands.ph[1]} ·{" "}
                    {selectedVariety.review_status}
                  </p>
                )}
              </div>
            )}

            {seasonalCrop && seasons.length > 0 && (
              <div className="mb-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
                <label className="text-sm font-semibold text-stone-800">
                  {t(lang, "seasonLabel")}
                </label>
                <select
                  value={effectiveSeason}
                  onChange={(e) => setSeason(e.target.value)}
                  className="mt-2 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
                >
                  {seasons.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.label}
                      {entry.months ? ` (${entry.months})` : ""}
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-xs text-stone-400">
                  {t(lang, "seasonCaption")}
                </p>
                {selectedSeason?.note && (
                  <p className="mt-2 text-xs text-stone-500">
                    {selectedSeason.note}
                  </p>
                )}
              </div>
            )}

            <div className="mb-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
              <label className="text-sm font-semibold text-stone-800">
                {t(lang, "agroZone")}
              </label>
              <select
                value={zone}
                onChange={(e) => setZone(e.target.value)}
                className="mt-2 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
              >
                <option value="">{t(lang, "agroZoneNone")}</option>
                {ZONE_OPTIONS.map((z) => (
                  <option key={z.id} value={z.id}>
                    {t(lang, z.labelKey)}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-xs text-stone-400">
                {t(lang, "agroZoneCaption")}
              </p>
            </div>

            <div className="mb-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
              <label className="text-sm font-semibold text-stone-800">
                {t(lang, "soilType")}
              </label>
              <select
                value={soilType}
                onChange={(e) => setSoilType(e.target.value)}
                className="mt-2 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
              >
                <option value="">{t(lang, "soilTypeNone")}</option>
                {SOIL_TYPE_OPTIONS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {t(lang, s.labelKey)}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-xs text-stone-400">
                {t(lang, "soilTypeCaption")}
              </p>
              {soilType && (
                <div className="mt-3">
                  <label className="text-xs font-semibold text-stone-600">
                    {t(lang, "limeType")}
                  </label>
                  <div className="mt-1 flex gap-2" role="radiogroup" aria-label={t(lang, "limeType")}>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={limeType === "calcitic"}
                      onClick={() => setLimeType("calcitic")}
                      className={
                        limeType === "calcitic"
                          ? "rounded-xl bg-forest px-3 py-1.5 text-xs font-semibold text-white"
                          : "rounded-xl border border-stone-200 bg-white px-3 py-1.5 text-xs font-medium text-stone-600"
                      }
                    >
                      {t(lang, "limeCalcitic")}
                    </button>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={limeType === "dolomitic"}
                      onClick={() => setLimeType("dolomitic")}
                      className={
                        limeType === "dolomitic"
                          ? "rounded-xl bg-forest px-3 py-1.5 text-xs font-semibold text-white"
                          : "rounded-xl border border-stone-200 bg-white px-3 py-1.5 text-xs font-medium text-stone-600"
                      }
                    >
                      {t(lang, "limeDolomitic")}
                    </button>
                  </div>
                </div>
              )}
            </div>

            <div className="mb-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
              <label className="text-sm font-semibold text-stone-800">
                {t(lang, "soilConditioningTitle")}
              </label>
              <p className="mt-1 text-xs text-stone-400">
                {t(lang, "soilConditioningCaption")}
              </p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="text-xs font-medium text-stone-500">
                    {t(lang, "textureClassLabel")}
                  </span>
                  <select
                    value={conditioning.texture_class ?? ""}
                    onChange={(e) =>
                      updateConditioning({
                        texture_class: e.target.value || null,
                      })
                    }
                    className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
                  >
                    <option value="">{t(lang, "soilConditioningNone")}</option>
                    {(conditioningRef?.texture_classes ?? []).map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {t(lang, TEXTURE_LABEL_KEYS[entry.id] ?? "") ||
                          entry.name_en}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block">
                  <span className="text-xs font-medium text-stone-500">
                    {t(lang, "socClassLabel")}
                  </span>
                  <select
                    value={conditioning.soc_class ?? ""}
                    onChange={(e) =>
                      updateConditioning({ soc_class: e.target.value || null })
                    }
                    className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
                  >
                    <option value="">{t(lang, "soilConditioningNone")}</option>
                    {(conditioningRef?.soc_classes ?? []).map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {t(lang, SOC_LABEL_KEYS[entry.id] ?? "") || entry.name_en}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block">
                  <span className="text-xs font-medium text-stone-500">
                    {t(lang, "socPctLabel")}
                  </span>
                  <input
                    type="number"
                    min={0}
                    max={20}
                    step={0.1}
                    value={conditioning.soc_pct ?? ""}
                    onChange={(e) =>
                      updateConditioning({
                        soc_pct:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    placeholder="e.g. 1.2"
                    className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
                  />
                </label>

                <label className="block">
                  <span className="text-xs font-medium text-stone-500">
                    {t(lang, "woredaLabel")}
                  </span>
                  <input
                    type="text"
                    value={conditioning.woreda ?? ""}
                    onChange={(e) =>
                      updateConditioning({ woreda: e.target.value || null })
                    }
                    placeholder={t(lang, "woredaPlaceholder")}
                    className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
                  />
                </label>
              </div>
              <p className="mt-2 text-xs text-stone-400">
                {t(lang, "soilConditioningNote")}
              </p>
            </div>

            <div className="mb-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
              <label className="text-sm font-semibold text-stone-800">
                {t(lang, "unitsToggle")}
              </label>
              <div className="mt-2 flex gap-2" role="radiogroup" aria-label={t(lang, "unitsToggle")}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={inputUnits === "kg_ha"}
                  onClick={() => setInputUnits("kg_ha")}
                  className={
                    inputUnits === "kg_ha"
                      ? "rounded-xl bg-forest px-4 py-2 text-xs font-semibold text-white"
                      : "rounded-xl border border-stone-200 bg-stone-50 px-4 py-2 text-xs font-semibold text-stone-500 transition hover:text-stone-800"
                  }
                >
                  {t(lang, "unitsKgHa")}
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={inputUnits === "lab"}
                  onClick={() => setInputUnits("lab")}
                  className={
                    inputUnits === "lab"
                      ? "rounded-xl bg-forest px-4 py-2 text-xs font-semibold text-white"
                      : "rounded-xl border border-stone-200 bg-stone-50 px-4 py-2 text-xs font-semibold text-stone-500 transition hover:text-stone-800"
                  }
                >
                  {t(lang, "unitsLab")}
                </button>
              </div>
              {inputUnits === "lab" && (
                <p className="mt-2 text-xs text-stone-400">{t(lang, "labCaption")}</p>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <NutrientSlider
                label={t(lang, "nitrogen")}
                unit={inputUnits === "lab" ? t(lang, "unitPct") : t(lang, "kgPerHa")}
                min={0}
                max={inputUnits === "lab" ? 2 : 160}
                step={inputUnits === "lab" ? 0.01 : 1}
                value={inputUnits === "lab" ? kgHaToPctN(values.N) : values.N}
                onChange={(v) => updateValue("N")(inputUnits === "lab" ? pctNToKgHa(v) : v)}
                accent="#14532d"
                icon={<Leaf size={20} />}
              />
              <NutrientSlider
                label={t(lang, "phosphorus")}
                unit={inputUnits === "lab" ? t(lang, "unitMgKg") : t(lang, "kgPerHa")}
                min={0}
                max={160}
                value={values.P}
                onChange={updateValue("P")}
                accent="#92400e"
                icon={<FlaskConical size={20} />}
              />
              <NutrientSlider
                label={t(lang, "potassium")}
                unit={inputUnits === "lab" ? t(lang, "unitCmolc") : t(lang, "kgPerHa")}
                min={0}
                max={inputUnits === "lab" ? 2 : 220}
                step={inputUnits === "lab" ? 0.01 : 1}
                value={inputUnits === "lab" ? kgHaToCmolcK(values.K) : values.K}
                onChange={(v) => updateValue("K")(inputUnits === "lab" ? cmolcKToKgHa(v) : v)}
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

          {result.warnings && result.warnings.length > 0 && (
            <div className="mb-5 rounded-2xl border border-gold/40 bg-gold/10 px-5 py-4 text-sm text-soil">
              <p className="font-semibold">{t(lang, "inputWarnings")}</p>
              <ul className="mt-1 list-inside list-disc space-y-0.5 opacity-90">
                {result.warnings.map((w) => (
                  <li key={w.field}>{w.message}</li>
                ))}
              </ul>
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

          {result.agroecology && (
            <div className="mt-5 rounded-2xl border border-forest/15 bg-forest/5 px-5 py-4 text-sm text-stone-700">
              <p className="font-semibold text-stone-900">
                {t(lang, "agroZone")}: {result.agroecology.name}
              </p>
              <p className="mt-1 text-xs text-stone-500">
                N {result.agroecology.bands.N[0]}–{result.agroecology.bands.N[1]} ·
                P {result.agroecology.bands.P[0]}–{result.agroecology.bands.P[1]} ·
                K {result.agroecology.bands.K[0]}–{result.agroecology.bands.K[1]} ·
                pH {result.agroecology.bands.ph[0]}–{result.agroecology.bands.ph[1]}
                {" "}kg/ha (0–20 cm)
              </p>
              <p className="mt-1.5 text-xs opacity-90">
                {result.agroecology.constraint}
              </p>
              {result.agroecology.salinity?.fertilizer_paused && (
                <p className="mt-2 rounded-xl bg-bad/10 px-3 py-2 text-xs font-semibold text-bad">
                  ⚠️ Salinity pause: ECe {result.agroecology.salinity.ec_e_ds_m} dS/m
                  &gt; {result.agroecology.salinity.pause_threshold_ds_m} dS/m — gypsum
                  + leaching instead of fertilizer.
                </p>
              )}
            </div>
          )}

          {result.season && (
            <div className="mt-5 rounded-2xl border border-forest/30 bg-forest/10 px-5 py-4 text-sm text-stone-700">
              <p className="font-semibold text-stone-900">
                {t(lang, "seasonLabel")}: {result.season.label}
              </p>
              <p className="mt-1 text-xs text-stone-600">
                {result.season.crop_label} · N {result.season.bands.N[0]}–
                {result.season.bands.N[1]} kg/ha · P{" "}
                {result.season.bands.P[0]}–{result.season.bands.P[1]} · K{" "}
                {result.season.bands.K[0]}–{result.season.bands.K[1]} · pH{" "}
                {result.season.bands.ph[0]}–{result.season.bands.ph[1]}
              </p>
              <p className="mt-1 text-xs text-stone-500">
                {t(lang, "seasonPNote")}
              </p>
              {result.season.note && (
                <p className="mt-2 text-xs opacity-80">{result.season.note}</p>
              )}
              <p className="mt-1 text-xs opacity-70">
                {result.season.source} · {result.season.review_status}
              </p>
            </div>
          )}

          {result.variety && (
            <div className="mt-4 rounded-2xl bg-forest/5 px-4 py-3">
              <p className="text-xs font-semibold text-forest">
                {t(lang, "varietyLabel")}: {result.variety.label}
              </p>
              <p className="mt-1 text-xs text-stone-600">
                {result.variety.crop_label} · {result.variety.region_label} ·{" "}
                N {result.variety.bands.N[0]}–{result.variety.bands.N[1]} · P{" "}
                {result.variety.bands.P[0]}–{result.variety.bands.P[1]} · K{" "}
                {result.variety.bands.K[0]}–{result.variety.bands.K[1]} · pH{" "}
                {result.variety.bands.ph[0]}–{result.variety.bands.ph[1]}
              </p>
              <p className="mt-1 text-xs opacity-80">
                {result.variety.source} · {result.variety.review_status}
              </p>
            </div>
          )}

          {result.soil_conditioning?.applied.length ? (
            <div className="mt-4 rounded-2xl bg-forest/5 px-4 py-3">
              <p className="text-xs font-semibold text-forest">
                {t(lang, "soilConditioningResult")}
              </p>
              <p className="mt-1 text-xs text-stone-600">
                {t(lang, "textureClassLabel")}:{" "}
                {result.soil_conditioning.texture_class ?? "—"} ·{" "}
                {t(lang, "socClassLabel")}:{" "}
                {result.soil_conditioning.soc_class ?? "—"}
                {result.soil_conditioning.soc_pct != null
                  ? ` (${result.soil_conditioning.soc_pct}%)`
                  : ""}
                {result.soil_conditioning.woreda
                  ? ` · ${result.soil_conditioning.woreda}`
                  : ""}
              </p>
              <ul className="mt-1 text-xs text-stone-600">
                {result.soil_conditioning.applied.map((entry) => (
                  <li key={entry.col}>
                    {entry.col}: {entry.before[0]}–{entry.before[1]} →{" "}
                    {entry.after[0]}–{entry.after[1]}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-xs opacity-80">
                {result.soil_conditioning.source ?? ""} ·{" "}
                {result.soil_conditioning.review_status ?? ""}
              </p>
            </div>
          ) : null}


          {result.model_version && (
            <p className="mt-3 text-right text-xs text-stone-400">
              model: {result.model_version}
              {result.llm_used ? ` · ${t(lang, "aiBadge")}` : ""}
            </p>
          )}

          <div className="mt-6">
            <ActionPlan
              recommendations={result.recommendations}
              lang={lang}
              limePlan={result.lime_plan}
              streaming={streaming}
              validatedCount={
                result.recommendations.filter((r) => r.source === "llm").length
              }
              totalCount={result.recommendations.length}
            />
          </div>

          <div className="mt-6">
            <SavePanel
              values={values}
              climate={climate}
              crop={crop === "generic" ? null : crop}
              result={result}
            />
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
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold capitalize text-stone-800">{match.crop}</p>
                        {match.suitability && (
                          <SuitabilityChip cls={match.suitability.class} lang={lang} />
                        )}
                      </div>
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

          {stapleSuitability && stapleSuitability.length > 0 && (
            <div className="mt-10">
              <h3 className="font-display text-2xl font-semibold text-stone-900">
                {t(lang, "ethiopianCrops")}
              </h3>
              <p className="mt-1 text-sm text-stone-500">{t(lang, "staplesHint")}</p>
              <p className="mt-0.5 text-xs text-stone-400">{t(lang, "staplesClimateHint")}</p>
              <div className="mt-4 space-y-2">
                {stapleSuitability.map((result, i) => {
                  const meta = stapleMeta[result.crop];
                  return (
                    <div
                      key={result.crop}
                      className="animate-fade-up flex items-center gap-4 rounded-2xl bg-white p-4 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]"
                      style={{ animationDelay: `${i * 20}ms` }}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold text-stone-800">
                            {meta?.name_en ?? result.crop}
                          </p>
                          <p className="text-xs text-stone-500">
                            {lang === "om" ? meta?.name_om : meta?.name_am}
                          </p>
                        </div>
                        {meta && (
                          <p className="mt-0.5 truncate text-xs text-stone-400">
                            {t(lang, "regionLabel")}: {meta.regions}
                          </p>
                        )}
                      </div>
                      <SuitabilityChip cls={result.class} lang={lang} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {stapleSuitability === null && (
            <div className="mt-10 flex items-center gap-2 text-sm text-stone-400">
              <Loader2 size={16} className="animate-spin" />
              {t(lang, "predicting")}
            </div>
          )}
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
