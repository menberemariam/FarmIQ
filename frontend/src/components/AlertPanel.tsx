"use client";

import Link from "next/link";
import { Check, TriangleAlert } from "lucide-react";
import { t, type Lang } from "@/i18n/strings";
import type { IotAlert } from "@/lib/api";

const TYPE_KEYS: Record<string, string> = {
  MOISTURE_CRITICAL: "alertMoistureCritical",
  MOISTURE_LOW: "alertMoistureLow",
  TOO_ACIDIC_SEVERE: "alertAcidicSevere",
  TOO_ACIDIC: "alertAcidic",
  TOO_ALKALINE_SEVERE: "alertAlkalineSevere",
  TOO_ALKALINE: "alertAlkaline",
  N_CRITICAL: "alertNCritical",
  N_LOW: "alertNLow",
  TEMP_STRESS: "alertTempStress",
  BATTERY_LOW: "alertBatteryLow",
  DATA_STALE: "alertDataStale",
  DATA_JUMP: "alertDataJump",
};

const SEVERITY_KEYS: Record<string, string> = {
  info: "severityInfo",
  warning: "severityWarning",
  critical: "severityCritical",
};

const SEVERITY_STYLES: Record<string, string> = {
  critical: "border-bad/40 bg-bad/10 text-bad",
  warning: "border-gold/40 bg-gold/10 text-soil",
  info: "border-stone-200 bg-stone-50 text-stone-500",
};

interface AlertPanelProps {
  alerts: IotAlert[];
  activeCount: number;
  onAck: (alertId: number) => void;
  lang: Lang;
}

export default function AlertPanel({ alerts, activeCount, onAck, lang }: AlertPanelProps) {
  return (
    <section
      id="alerts"
      className="mt-6 rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-stone-800">
          <TriangleAlert size={16} className="text-gold" />
          {t(lang, "alertsTitle")}
        </h2>
        <span
          className={`inline-flex h-6 min-w-6 items-center justify-center rounded-full px-2 text-xs font-bold tabular-nums ${
            activeCount > 0 ? "bg-bad text-white" : "bg-stone-100 text-stone-500"
          }`}
        >
          {activeCount}
        </span>
      </div>
      <p className="mt-1 text-xs text-stone-400">{t(lang, "alertsHint")}</p>

      {alerts.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-stone-200 px-4 py-5 text-center text-sm text-stone-400">
          {t(lang, "alertsNone")}
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-stone-100">
          {alerts.map((alert) => (
            <li key={alert.id} className="flex flex-wrap items-start gap-3 py-3">
              <span
                className={`mt-0.5 shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
                  SEVERITY_STYLES[alert.severity] ?? SEVERITY_STYLES.info
                }`}
              >
                {t(lang, SEVERITY_KEYS[alert.severity] ?? "severityInfo")}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-stone-800">
                  {t(lang, TYPE_KEYS[alert.alert_type] ?? alert.alert_type)}
                </p>
                <p className="mt-0.5 break-words text-xs text-stone-500">
                  {alert.device_name} ({alert.device_public_id})
                  {alert.value !== null && (
                    <span className="tabular-nums">
                      {" · "}
                      {alert.value}
                      {alert.threshold !== null ? ` / ${alert.threshold}` : ""}
                    </span>
                  )}
                  {" · "}
                  {new Date(alert.created_at).toLocaleString()}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {alert.farmer_id !== null && (
                  <Link
                    href={`/agent/${alert.farmer_id}`}
                    className="text-xs font-medium text-forest underline hover:brightness-110"
                  >
                    {t(lang, "alertOpenFarmer")}
                  </Link>
                )}
                {alert.status === "active" && (
                  <button
                    onClick={() => onAck(alert.id)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-2.5 py-1 text-xs font-medium text-stone-600 transition hover:bg-stone-50"
                  >
                    <Check size={12} /> {t(lang, "alertAck")}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
