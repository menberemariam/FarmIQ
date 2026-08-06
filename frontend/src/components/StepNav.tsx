"use client";

import { Leaf, BarChart3, Sprout } from "lucide-react";
import type { Lang } from "@/i18n/strings";
import { t } from "@/i18n/strings";

const STEPS = [
  { key: 1, icon: Leaf },
  { key: 2, icon: BarChart3 },
  { key: 3, icon: Sprout },
];

interface StepNavProps {
  step: number;
  lang: Lang;
}

export default function StepNav({ step, lang }: StepNavProps) {
  return (
    <nav className="mx-auto flex w-full max-w-2xl items-center justify-center gap-2 px-4 py-6 sm:gap-3">
      {STEPS.map(({ key, icon: Icon }, i) => {
        const active = step === key;
        const done = step > key;
        return (
          <div key={key} className="flex items-center gap-2 sm:gap-3">
            {i > 0 && (
              <div
                className={`h-0.5 w-8 rounded-full sm:w-14 ${done ? "bg-forest" : "bg-stone-300"}`}
              />
            )}
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={`flex size-11 items-center justify-center rounded-full border-2 transition-all duration-300 ${
                  active
                    ? "border-forest bg-forest text-white shadow-lg"
                    : done
                      ? "border-forest bg-white text-forest"
                      : "border-stone-300 bg-white text-stone-400"
                }`}
              >
                <Icon size={20} />
              </div>
              <span
                className={`text-xs font-medium ${active ? "text-forest" : "text-stone-400"}`}
              >
                {t(lang, `step${key}`)}
              </span>
            </div>
          </div>
        );
      })}
    </nav>
  );
}
