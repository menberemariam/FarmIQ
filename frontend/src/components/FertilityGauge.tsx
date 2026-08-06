"use client";

import type { FertilityStatus } from "@/lib/api";
import { useCountUp } from "@/lib/useCountUp";

const COLOR: Record<FertilityStatus, string> = {
  Good: "#059669",
  Moderate: "#d97706",
  Poor: "#e11d48",
};

interface FertilityGaugeProps {
  status: FertilityStatus;
  confidence: number; // 0-100
  statusLabel: string;
  confidenceLabel: string;
}

export default function FertilityGauge({
  status,
  confidence,
  statusLabel,
  confidenceLabel,
}: FertilityGaugeProps) {
  const radius = 80;
  const circumference = 2 * Math.PI * radius;
  const animated = useCountUp(confidence);
  const color = COLOR[status];

  return (
    <div className="flex flex-col items-center rounded-2xl bg-white p-8 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <div className="relative">
        <svg width="200" height="200" viewBox="0 0 200 200" className="-rotate-90">
          <circle
            cx="100"
            cy="100"
            r={radius}
            fill="none"
            stroke="#e7e5e4"
            strokeWidth="14"
          />
          <circle
            cx="100"
            cy="100"
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth="14"
            strokeLinecap="round"
            strokeDasharray={`${(animated / 100) * circumference} ${circumference}`}
            style={{ transition: "stroke-dasharray 0.1s linear" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="font-display text-5xl font-semibold tabular-nums"
            style={{ color }}
          >
            {Math.round(animated)}
            <span className="text-2xl">%</span>
          </span>
          <span className="mt-1 text-xs font-medium uppercase tracking-wider text-stone-400">
            {confidenceLabel}
          </span>
        </div>
      </div>
      <div
        className="mt-5 rounded-full px-5 py-2 text-lg font-semibold"
        style={{ backgroundColor: `${color}1a`, color }}
      >
        {statusLabel}
      </div>
    </div>
  );
}
