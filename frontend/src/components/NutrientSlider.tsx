"use client";

interface NutrientSliderProps {
  label: string;
  unit: string;
  min: number;
  max: number;
  step?: number;
  value: number;
  onChange: (value: number) => void;
  accent: string;
  icon: React.ReactNode;
}

export default function NutrientSlider({
  label,
  unit,
  min,
  max,
  step = 1,
  value,
  onChange,
  accent,
  icon,
}: NutrientSliderProps) {
  const fillPercent = ((value - min) / (max - min)) * 100;

  return (
    <div className="rounded-2xl bg-white p-5 shadow-[0_4px_20px_-2px_rgba(0,0,0,0.05)]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className="flex size-10 items-center justify-center rounded-xl text-white"
            style={{ backgroundColor: accent }}
          >
            {icon}
          </span>
          <div>
            <p className="text-sm font-semibold text-stone-800">{label}</p>
            <p className="text-xs text-stone-400">{unit}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={min}
            max={max}
            step={step}
            value={value}
            onChange={(e) =>
              onChange(Math.min(max, Math.max(min, Number(e.target.value) || min)))
            }
            className="w-20 rounded-xl border border-stone-200 bg-stone-50 px-2 py-1.5 text-right text-sm font-semibold text-stone-800 outline-none focus:border-forest focus:ring-2 focus:ring-forest/20"
          />
        </div>
      </div>
      <input
        type="range"
        className="soil-slider mt-4"
        style={{
          ["--thumb-color" as string]: accent,
          background: `linear-gradient(to right, ${accent} ${fillPercent}%, #e7e5e4 ${fillPercent}%)`,
        }}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={label}
      />
    </div>
  );
}
