"use client";

import { BatteryMedium, Radio, Wifi } from "lucide-react";
import { t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type { IotDevice, IotDeviceStatus } from "@/lib/api";

const STATUS_DOT: Record<IotDeviceStatus, string> = {
  online: "bg-emerald-500",
  offline: "bg-amber-500",
  never_seen: "bg-stone-300",
};

function relativeAgo(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export default function IoTStatusCard({ device }: { device: IotDevice }) {
  const { lang } = useLang();
  const statusLabel = t(
    lang,
    device.status === "online"
      ? "iotOnline"
      : device.status === "offline"
        ? "iotOffline"
        : "iotNeverSeen",
  );

  return (
    <div className="rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-forest/10">
          <Radio size={18} className="text-forest" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-stone-800">
            {device.name}
          </p>
          <p className="truncate text-xs text-stone-400">
            {device.device_id} · {t(lang, "iotPlot")}: {device.plot_name ?? "—"}
          </p>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full bg-stone-50 px-3 py-1 text-xs font-medium text-stone-600">
          <span
            className={`size-2 rounded-full ${STATUS_DOT[device.status]} ${
              device.status === "online" ? "animate-pulse" : ""
            }`}
          />
          {statusLabel}
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-xl bg-stone-50 p-3">
          <dt className="text-[11px] text-stone-400">{t(lang, "iotLastSeen")}</dt>
          <dd className="mt-0.5 text-sm font-semibold tabular-nums text-stone-700">
            {relativeAgo(device.seconds_since_last)}
          </dd>
        </div>
        <div className="rounded-xl bg-stone-50 p-3">
          <dt className="inline-flex items-center gap-1 text-[11px] text-stone-400">
            <BatteryMedium size={12} /> {t(lang, "iotBattery")}
          </dt>
          <dd className="mt-0.5 text-sm font-semibold tabular-nums text-stone-700">
            {device.battery !== null ? `${device.battery}%` : "—"}
          </dd>
        </div>
        <div className="rounded-xl bg-stone-50 p-3">
          <dt className="inline-flex items-center gap-1 text-[11px] text-stone-400">
            <Wifi size={12} /> {t(lang, "iotSignal")}
          </dt>
          <dd className="mt-0.5 text-sm font-semibold tabular-nums text-stone-700">
            {device.signal !== null ? `${device.signal} dBm` : "—"}
          </dd>
        </div>
        <div className="rounded-xl bg-stone-50 p-3">
          <dt className="text-[11px] text-stone-400">{t(lang, "iotFirmware")}</dt>
          <dd className="mt-0.5 text-sm font-semibold text-stone-700">
            {device.firmware}
          </dd>
        </div>
      </dl>
    </div>
  );
}
