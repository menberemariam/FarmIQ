"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Save } from "lucide-react";
import { t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type {
  ClimateValues,
  Farmer,
  Plot,
  PredictResponse,
  SoilValues,
} from "@/lib/api";
import {
  createFarmer,
  createPlot,
  fetchFarmers,
  fetchFarmer,
  fetchHealth,
  saveReportWithLinks,
} from "@/lib/api";

const NO_FARMER = "";
const NEW_FARMER = "__new_farmer__";
const NEW_PLOT = "__new_plot__";

interface SavePanelProps {
  values: SoilValues;
  climate: ClimateValues;
  crop: string | null;
  result: PredictResponse;
}

type SaveStatus = "idle" | "saving" | "saved" | "error";

export default function SavePanel({ values, climate, crop, result }: SavePanelProps) {
  const { lang } = useLang();
  const [farmers, setFarmers] = useState<Farmer[]>([]);
  const [farmerChoice, setFarmerChoice] = useState<string>(NO_FARMER);
  const [newFarmerName, setNewFarmerName] = useState("");
  const [plots, setPlots] = useState<Plot[]>([]);
  const [plotChoice, setPlotChoice] = useState<string>(NEW_PLOT);
  const [plotName, setPlotName] = useState("");
  const [plotSize, setPlotSize] = useState("");
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [savedFarmerId, setSavedFarmerId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Opt-in training-data consent. Both default to off: the checkbox is only
  // rendered when the deployment reports capture enabled, and the payload
  // omits the field entirely unless the user ticks it.
  const [captureEnabled, setCaptureEnabled] = useState(false);
  const [consent, setConsent] = useState(false);

  useEffect(() => {
    fetchFarmers()
      .then((data) => setFarmers(data.farmers))
      .catch(() => setFarmers([]));
    fetchHealth()
      .then((health) => setCaptureEnabled(Boolean(health?.training_capture)))
      .catch(() => setCaptureEnabled(false));
  }, []);

  const linkedFarmerId =
    farmerChoice !== NO_FARMER && farmerChoice !== NEW_FARMER
      ? Number(farmerChoice)
      : null;

  const onFarmerChange = useCallback((value: string) => {
    setFarmerChoice(value);
    setPlotChoice(NEW_PLOT);
    setError(null);
    if (value !== NO_FARMER && value !== NEW_FARMER) {
      fetchFarmer(Number(value))
        .then((detail) => setPlots(detail.plots))
        .catch(() => setPlots([]));
    } else {
      setPlots([]);
    }
  }, []);

  const save = useCallback(async () => {
    setError(null);
    setStatus("saving");
    try {
      let farmerId = linkedFarmerId;

      if (farmerChoice === NEW_FARMER) {
        if (!newFarmerName.trim()) {
          setError(t(lang, "required"));
          setStatus("error");
          return;
        }
        const created = await createFarmer({ name: newFarmerName.trim(), lang });
        farmerId = created.id;
      }

      let plotId: number | null = null;
      let plotLabel: string;

      if (farmerId) {
        if (plotChoice === NEW_PLOT) {
          if (!plotName.trim()) {
            setError(t(lang, "required"));
            setStatus("error");
            return;
          }
          const created = await createPlot({
            farmer_id: farmerId,
            name: plotName.trim(),
            farm_size_ha: plotSize.trim() ? Number(plotSize) : null,
          });
          plotId = created.id;
          plotLabel = created.plot.name;
        } else {
          const existing = plots.find((p) => String(p.id) === plotChoice);
          if (!existing) {
            setError(t(lang, "required"));
            setStatus("error");
            return;
          }
          plotId = existing.id;
          plotLabel = existing.name;
        }
      } else {
        plotLabel = plotName.trim();
        if (!plotLabel) {
          setError(t(lang, "required"));
          setStatus("error");
          return;
        }
      }

      await saveReportWithLinks({
        report: values,
        plot: plotLabel,
        fertility_status: result.fertility_status,
        recommendations: result.recommendations,
        crop: result.season?.crop ?? result.variety?.crop ?? crop,
        climate,
        crops: result.crops,
        lang,
        farmer_id: farmerId,
        plot_id: plotId,
        agroecological_zone: result.agroecology?.zone ?? null,
        ...(consent ? { consent_for_training: true } : {}),
        variety: result.variety?.label ?? null,
        season: result.season?.season ?? null,
        region: result.variety?.region ?? null,
        conditioning: result.soil_conditioning
          ? {
              texture_class: result.soil_conditioning.texture_class,
              soc_pct: result.soil_conditioning.soc_pct,
              soc_class: result.soil_conditioning.soc_class,
              woreda: result.soil_conditioning.woreda,
            }
          : undefined,
      });

      setSavedFarmerId(farmerId);
      setStatus("saved");
      if (farmerId) {
        fetchFarmers()
          .then((data) => setFarmers(data.farmers))
          .catch(() => {});
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("error");
    }
  }, [
    farmerChoice, linkedFarmerId, newFarmerName, plotChoice, plotName, plotSize,
    plots, values, result, crop, climate, lang, consent,
  ]);

  if (status === "saved") {
    return (
      <div className="rounded-2xl border border-good/30 bg-good/10 px-5 py-4 text-sm">
        <p className="font-semibold text-good">{t(lang, "saved")}</p>
        {savedFarmerId && (
          <Link
            href={`/agent/${savedFarmerId}`}
            className="mt-1 inline-block font-medium text-forest underline-offset-2 hover:underline"
          >
            {t(lang, "savedTo")}
          </Link>
        )}
        <button
          onClick={() => setStatus("idle")}
          className="mt-3 rounded-xl bg-forest px-4 py-2 text-xs font-semibold text-white transition hover:brightness-110"
        >
          {t(lang, "saveReport")}
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <p className="text-sm font-semibold text-stone-800">{t(lang, "saveTitle")}</p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="text-xs font-medium text-stone-500">{t(lang, "linkFarmer")}</span>
          <select
            value={farmerChoice}
            onChange={(e) => onFarmerChange(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest"
          >
            <option value={NO_FARMER}>{t(lang, "notLinked")}</option>
            {farmers.map((f) => (
              <option key={f.id} value={String(f.id)}>
                {f.name}
                {f.region ? ` · ${f.region}` : ""}
              </option>
            ))}
            <option value={NEW_FARMER}>{t(lang, "newFarmerInline")}</option>
          </select>
        </label>

        {farmerChoice === NEW_FARMER ? (
          <label className="block">
            <span className="text-xs font-medium text-stone-500">{t(lang, "farmerName")}</span>
            <input
              value={newFarmerName}
              onChange={(e) => setNewFarmerName(e.target.value)}
              placeholder={t(lang, "farmerName")}
              className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest"
            />
          </label>
        ) : linkedFarmerId ? (
          <label className="block">
            <span className="text-xs font-medium text-stone-500">{t(lang, "plots")}</span>
            <select
              value={plotChoice}
              onChange={(e) => setPlotChoice(e.target.value)}
              className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-800 outline-none focus:border-forest"
            >
              {plots.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                  {p.farm_size_ha != null ? ` · ${p.farm_size_ha} ha` : ""}
                </option>
              ))}
              <option value={NEW_PLOT}>{t(lang, "newPlot")}</option>
            </select>
          </label>
        ) : (
          <label className="block">
            <span className="text-xs font-medium text-stone-500">{t(lang, "plotName")}</span>
            <input
              value={plotName}
              onChange={(e) => setPlotName(e.target.value)}
              placeholder={t(lang, "plotName")}
              className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest"
            />
          </label>
        )}

        {linkedFarmerId && plotChoice === NEW_PLOT && (
          <>
            <label className="block">
              <span className="text-xs font-medium text-stone-500">{t(lang, "plotName")}</span>
              <input
                value={plotName}
                onChange={(e) => setPlotName(e.target.value)}
                placeholder={t(lang, "plotName")}
                className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest"
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-stone-500">{t(lang, "farmSize")}</span>
              <input
                type="number"
                min={0}
                step="0.1"
                value={plotSize}
                onChange={(e) => setPlotSize(e.target.value)}
                placeholder="2.1"
                className="mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest"
              />
            </label>
          </>
        )}
      </div>

      {captureEnabled && (
        <label className="mt-4 flex items-start gap-2.5 rounded-xl border border-stone-200 bg-stone-50 px-3.5 py-3">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 rounded border-stone-300 accent-[#14532d]"
          />
          <span>
            <span className="text-xs font-semibold text-stone-700">
              {t(lang, "trainingConsent")}
            </span>
            <span className="mt-0.5 block text-[11px] leading-relaxed text-stone-500">
              {t(lang, "trainingConsentHint")}
            </span>
          </span>
        </label>
      )}

      {error && (
        <p className="mt-3 rounded-xl border border-bad/30 bg-bad/10 px-4 py-2.5 text-sm text-bad">
          <span className="font-semibold">{t(lang, "saveError")}</span> {error}
        </p>
      )}

      <button
        onClick={save}
        disabled={status === "saving"}
        className="mt-4 inline-flex items-center gap-2 rounded-2xl bg-forest px-6 py-3 text-sm font-semibold text-white shadow transition hover:brightness-110 active:scale-[0.99] disabled:opacity-70"
      >
        {status === "saving" ? (
          <Loader2 size={16} className="animate-spin" />
        ) : (
          <Save size={16} />
        )}
        {t(lang, "saveReport")}
      </button>
    </div>
  );
}
