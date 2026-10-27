"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  CloudSun,
  Database,
  Download,
  Loader2,
  Play,
  Save,
  Sparkles,
  Square,
} from "lucide-react";
import AssessmentPanel from "@/components/AssessmentPanel";
import AlertPanel from "@/components/AlertPanel";
import IoTStatusCard from "@/components/IoTStatusCard";
import ReadingGrid from "@/components/ReadingGrid";
import TrendChart from "@/components/TrendChart";
import { t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type {
  DeviceAssessment,
  IotAlert,
  IotDevice,
  IotReading,
  SimulationScenario,
  SimulationState,
} from "@/lib/api";
import {
  ackAlert,
  controlSimulation,
  fetchAlerts,
  fetchDeviceAssessment,
  fetchDevices,
  fetchReadings,
  fetchSimulation,
  readingsExportUrl,
  runDemo,
  saveReadingReport,
} from "@/lib/api";

const SCENARIOS: { id: SimulationScenario; labelKey: string }[] = [
  { id: "normal", labelKey: "scenarioNormal" },
  { id: "nitrogen_deficient", labelKey: "scenarioNitrogen" },
  { id: "acidic", labelKey: "scenarioAcidic" },
  { id: "dry", labelKey: "scenarioDry" },
];

export default function IoTPage() {
  const { lang } = useLang();
  const [devices, setDevices] = useState<IotDevice[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [readings, setReadings] = useState<IotReading[]>([]);
  const [assessment, setAssessment] = useState<DeviceAssessment | null>(null);
  const [simulation, setSimulation] = useState<SimulationState | null>(null);
  const [scenario, setScenario] = useState<SimulationScenario>("normal");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedReadingId, setSavedReadingId] = useState<number | null>(null);
  const [savedFarmerId, setSavedFarmerId] = useState<number | null>(null);
  const [alerts, setAlerts] = useState<IotAlert[]>([]);
  const [alertCount, setAlertCount] = useState(0);
  const [runningDemo, setRunningDemo] = useState(false);
  const [demoNote, setDemoNote] = useState<number | null>(null);

  const load = useCallback(() => {
    Promise.all([
      fetchDevices(),
      selectedId ? fetchReadings(selectedId, 30) : Promise.resolve(null),
      selectedId
        ? fetchDeviceAssessment(selectedId, lang).catch(() => null)
        : Promise.resolve(null),
      fetchSimulation().catch(() => null),
      fetchAlerts({ limit: 20 }).catch(() => null),
    ])
      .then(([devicesRes, readingsRes, assessmentRes, simulationRes, alertsRes]) => {
        const list = devicesRes.devices;
        setDevices(list);
        const active =
          list.find((d) => d.device_id === selectedId) ?? list[0] ?? null;
        if (active && active.device_id !== selectedId) {
          setSelectedId(active.device_id); // next cycle fetches its readings
          setReadings([]);
          setAssessment(null);
        } else if (readingsRes) {
          setReadings(readingsRes.readings);
        }
        if (assessmentRes) setAssessment(assessmentRes);
        else if (selectedId) setAssessment(null);
        if (simulationRes) setSimulation(simulationRes.simulation);
        if (alertsRes) {
          setAlerts(alertsRes.alerts);
          setAlertCount(alertsRes.active_count);
        }
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [selectedId, lang]);

  useEffect(() => {
    load();
    const id = setInterval(() => {
      if (!document.hidden) load();
    }, 5000);
    return () => clearInterval(id);
  }, [load]);

  const applyScenario = useCallback(
    (next: SimulationScenario) => {
      setScenario(next);
      if (simulation?.running) {
        controlSimulation("start", {
          scenario: next,
          ...(selectedId ? { device_id: selectedId } : {}),
        })
          .then((res) => setSimulation(res.simulation))
          .catch((err: unknown) =>
            setError(err instanceof Error ? err.message : String(err)),
          );
      }
    },
    [simulation?.running, selectedId],
  );

  const toggleSimulation = useCallback(() => {
    const action: "start" | "stop" = simulation?.running ? "stop" : "start";
    controlSimulation(action, {
      scenario,
      ...(action === "start" && selectedId ? { device_id: selectedId } : {}),
    })
      .then((res) => {
        setSimulation(res.simulation);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      );
  }, [simulation?.running, scenario, selectedId]);

  const latest = readings[0] ?? null;
  const saveLatest = useCallback(() => {
    if (!latest) return;
    setSaving(true);
    saveReadingReport(latest.id, lang)
      .then((res) => {
        setSavedReadingId(latest.id);
        setSavedFarmerId(res.farmer_id);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setSaving(false));
  }, [latest, lang]);

  const chartPoints = (pick: (r: IotReading) => number) =>
    readings.map((r) => ({ ts: r.reading_ts, v: pick(r) }));

  const ackOne = useCallback(
    (alertId: number) => {
      ackAlert(alertId)
        .then(() => {
          setError(null);
          load();
        })
        .catch((err: unknown) =>
          setError(err instanceof Error ? err.message : String(err)),
        );
    },
    [load],
  );

  const runDemoNow = useCallback(() => {
    setRunningDemo(true);
    setDemoNote(null);
    runDemo({ scenario, count: 20 })
      .then((res) => {
        setDemoNote(res.readings_created);
        setSelectedId(res.device_id);
        setError(null);
        load();
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setRunningDemo(false));
  }, [scenario, load]);

  return (
    <main className="mx-auto w-full max-w-4xl px-6 pb-20 pt-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-display text-3xl font-semibold text-stone-900">
          {t(lang, "iotTitle")}
        </h1>
        <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700">
          {t(lang, "iotSimulatedBadge")}
        </span>
        <span className="inline-flex items-center gap-1.5 text-xs text-stone-400">
          <Activity size={13} className="text-emerald-500" />
          {t(lang, "iotLiveNote")}
        </span>
      </div>
      <p className="mt-2 text-sm text-stone-500">{t(lang, "iotSubtitle")}</p>

      {error && (
        <div className="mt-6 rounded-2xl border border-bad/30 bg-bad/10 px-5 py-4 text-sm text-bad">
          {error}
        </div>
      )}

      {/* Toolbar: reproducible demo + CSV export (Phase 3) */}
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button
          onClick={runDemoNow}
          disabled={runningDemo}
          className="inline-flex items-center gap-2 rounded-xl bg-forest px-4 py-2 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-60"
        >
          {runningDemo ? (
            <Loader2 size={15} className="animate-spin" />
          ) : (
            <Sparkles size={15} />
          )}
          {t(lang, "runDemo")}
        </button>
        <span className="text-xs text-stone-400">{t(lang, "runDemoHint")}</span>
        {demoNote !== null && (
          <span className="text-sm font-medium text-forest">
            {t(lang, "runDemoDone")} {demoNote} {t(lang, "readingsLoaded")}
          </span>
        )}
        {selectedId && (
          <a
            href={readingsExportUrl(selectedId)}
            className="ml-auto inline-flex items-center gap-2 rounded-xl border border-stone-200 px-4 py-2 text-sm font-medium text-stone-600 transition hover:bg-stone-50"
          >
            <Download size={15} /> {t(lang, "csvExport")}
          </a>
        )}
      </div>

      {loading && devices.length === 0 && (
        <p className="mt-8 inline-flex items-center gap-2 text-sm text-stone-500">
          <Loader2 size={16} className="animate-spin" /> {t(lang, "loading")}
        </p>
      )}

      {!loading && devices.length === 0 && !error && (
        <p className="mt-8 rounded-2xl border border-dashed border-stone-300 px-5 py-8 text-center text-sm text-stone-400">
          {t(lang, "iotNoDevices")}
        </p>
      )}

      {/* IoT → Farmer advisory alerts */}
      {!loading && (
        <AlertPanel
          alerts={alerts}
          activeCount={alertCount}
          onAck={ackOne}
          lang={lang}
        />
      )}

      {devices.length > 1 && (
        <div className="mt-5 flex flex-wrap gap-2">
          {devices.map((d) => (
            <button
              key={d.id}
              onClick={() => setSelectedId(d.device_id)}
              className={`rounded-xl px-3 py-1.5 text-sm font-medium transition ${
                d.device_id === selectedId
                  ? "bg-forest/10 text-forest"
                  : "border border-stone-200 text-stone-500 hover:bg-stone-50"
              }`}
            >
              {d.device_id}
            </button>
          ))}
        </div>
      )}

      {devices.length > 0 && selectedId && (
        <>
          <div className="mt-5">
            <IoTStatusCard
              device={
                devices.find((d) => d.device_id === selectedId) ?? devices[0]
              }
            />
          </div>

          {/* Simulator controls */}
          <div className="mt-4 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-stone-800">
                  {t(lang, "iotScenario")}
                </p>
                <p className="mt-0.5 text-xs text-stone-400">
                  {t(lang, "iotSimHint")}
                </p>
              </div>
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${
                  simulation?.running
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-stone-100 text-stone-500"
                }`}
              >
                <span
                  className={`size-1.5 rounded-full ${
                    simulation?.running ? "animate-pulse bg-emerald-500" : "bg-stone-400"
                  }`}
                />
                {simulation?.running
                  ? t(lang, "iotSimRunning")
                  : t(lang, "iotSimStopped")}
                {simulation?.running && (
                  <span className="tabular-nums">
                    · {t(lang, "iotSimTicks")} {simulation.ticks}
                  </span>
                )}
              </span>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              {SCENARIOS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => applyScenario(s.id)}
                  className={`rounded-xl px-3 py-1.5 text-sm font-medium transition ${
                    scenario === s.id
                      ? "bg-forest text-white"
                      : "border border-stone-200 text-stone-600 hover:bg-stone-50"
                  }`}
                >
                  {t(lang, s.labelKey)}
                </button>
              ))}
              <button
                onClick={toggleSimulation}
                className={`ml-auto inline-flex items-center gap-2 rounded-xl px-4 py-1.5 text-sm font-semibold transition ${
                  simulation?.running
                    ? "border border-bad/40 bg-bad/10 text-bad hover:bg-bad/15"
                    : "bg-forest text-white hover:brightness-110"
                }`}
              >
                {simulation?.running ? (
                  <>
                    <Square size={14} /> {t(lang, "iotStopSim")}
                  </>
                ) : (
                  <>
                    <Play size={14} /> {t(lang, "iotStartSim")}
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Latest reading */}
          <div className="mt-4">
            {latest ? (
              <ReadingGrid reading={latest} />
            ) : (
              <p className="rounded-2xl border border-dashed border-stone-300 px-5 py-8 text-center text-sm text-stone-400">
                {t(lang, "iotNoReadings")}
              </p>
            )}
          </div>

          {latest && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                onClick={saveLatest}
                disabled={saving || savedReadingId === latest.id}
                className="inline-flex items-center gap-2 rounded-xl bg-forest px-4 py-2 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-60"
              >
                {saving ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : (
                  <Save size={15} />
                )}
                {t(lang, "iotSaveReading")}
              </button>
              {savedReadingId === latest.id && (
                <span className="inline-flex items-center gap-2 text-sm text-forest">
                  {t(lang, "iotSavedReading")}
                  {savedFarmerId !== null && (
                    <Link
                      href={`/agent/${savedFarmerId}`}
                      className="underline hover:brightness-110"
                    >
                      {t(lang, "iotOpenFarmer")}
                    </Link>
                  )}
                </span>
              )}
            </div>
          )}

          {/* Trend charts */}
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <TrendChart
              title={t(lang, "nitrogen")}
              unit={t(lang, "kgPerHa")}
              points={chartPoints((r) => r.N)}
              color="#14532d"
              emptyLabel={t(lang, "iotNoReadings")}
            />
            <TrendChart
              title={t(lang, "iotMoisture")}
              unit="%"
              points={chartPoints((r) => r.moisture)}
              color="#0284c7"
              emptyLabel={t(lang, "iotNoReadings")}
            />
            <TrendChart
              title={t(lang, "soilPh")}
              unit="pH"
              points={chartPoints((r) => r.ph)}
              color="#059669"
              emptyLabel={t(lang, "iotNoReadings")}
            />
            <TrendChart
              title={t(lang, "temp")}
              unit="°C"
              points={chartPoints((r) => r.temperature)}
              color="#dc2626"
              emptyLabel={t(lang, "iotNoReadings")}
            />
          </div>

          {/* Live assessment */}
          <div className="mt-8">
            {assessment ? (
              <AssessmentPanel assessment={assessment} />
            ) : (
              <p className="inline-flex items-center gap-2 text-sm text-stone-400">
                <CloudSun size={15} />
                {latest ? t(lang, "loading") : t(lang, "iotNoReadings")}
              </p>
            )}
          </div>

          <p className="mt-8 inline-flex items-center gap-2 text-xs text-stone-400">
            <Database size={13} /> {t(lang, "disclaimer")}
          </p>
        </>
      )}
    </main>
  );
}
