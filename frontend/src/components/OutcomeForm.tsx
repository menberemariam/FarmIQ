"use client";

import { useCallback, useState } from "react";
import { Loader2 } from "lucide-react";
import { t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import type { Outcome } from "@/lib/api";
import { saveOutcome } from "@/lib/api";

interface OutcomeFormProps {
  reportId: number;
  initial?: Outcome | null;
  onSaved: () => void;
}

export default function OutcomeForm({ reportId, initial, onSaved }: OutcomeFormProps) {
  const { lang } = useLang();
  const [implementedAt, setImplementedAt] = useState(initial?.implemented_at ?? "");
  const [notes, setNotes] = useState(initial?.implementation_notes ?? "");
  const [expectedYield, setExpectedYield] = useState(
    initial?.expected_yield != null ? String(initial.expected_yield) : "",
  );
  const [actualYield, setActualYield] = useState(
    initial?.actual_yield != null ? String(initial.actual_yield) : "",
  );
  const [yieldUnit, setYieldUnit] = useState(initial?.yield_unit ?? "kg/ha");
  const [harvestAt, setHarvestAt] = useState(initial?.harvest_at ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useCallback(async () => {
    setError(null);
    setSaving(true);
    try {
      await saveOutcome(reportId, {
        implementation_notes: notes.trim() || null,
        implemented_at: implementedAt || null,
        expected_yield: expectedYield.trim() ? Number(expectedYield) : null,
        actual_yield: actualYield.trim() ? Number(actualYield) : null,
        yield_unit: yieldUnit.trim() || "kg/ha",
        harvest_at: harvestAt || null,
      });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [
    reportId, notes, implementedAt, expectedYield, actualYield, yieldUnit, harvestAt, onSaved,
  ]);

  const fieldClass =
    "mt-1.5 w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-800 outline-none focus:border-forest";
  const labelClass = "text-xs font-medium text-stone-500";

  return (
    <div className="mt-4 rounded-2xl border border-stone-200 bg-stone-50/70 p-4">
      <p className="text-sm font-semibold text-stone-800">{t(lang, "outcomeTitle")}</p>

      <div className="mt-3 grid gap-4 sm:grid-cols-3">
        <label className="block">
          <span className={labelClass}>{t(lang, "implementedDate")}</span>
          <input
            type="date"
            value={implementedAt}
            onChange={(e) => setImplementedAt(e.target.value)}
            className={fieldClass}
          />
        </label>
        <label className="block">
          <span className={labelClass}>{t(lang, "expectedYield")}</span>
          <input
            type="number"
            min={0}
            step="0.1"
            value={expectedYield}
            onChange={(e) => setExpectedYield(e.target.value)}
            className={fieldClass}
          />
        </label>
        <label className="block">
          <span className={labelClass}>{t(lang, "actualYield")}</span>
          <input
            type="number"
            min={0}
            step="0.1"
            value={actualYield}
            onChange={(e) => setActualYield(e.target.value)}
            className={fieldClass}
          />
        </label>
        <label className="block">
          <span className={labelClass}>{t(lang, "harvestDate")}</span>
          <input
            type="date"
            value={harvestAt}
            onChange={(e) => setHarvestAt(e.target.value)}
            className={fieldClass}
          />
        </label>
        <label className="block">
          <span className={labelClass}>{t(lang, "yieldUnit")}</span>
          <input
            value={yieldUnit}
            onChange={(e) => setYieldUnit(e.target.value)}
            placeholder="kg/ha"
            className={fieldClass}
          />
        </label>
        <label className="block sm:col-span-3">
          <span className={labelClass}>{t(lang, "outcomeNotes")}</span>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            className={fieldClass}
          />
        </label>
      </div>

      {error && (
        <p className="mt-3 rounded-xl border border-bad/30 bg-bad/10 px-4 py-2.5 text-sm text-bad">
          {error}
        </p>
      )}
      {saved && !error && (
        <p className="mt-3 rounded-xl border border-good/30 bg-good/10 px-4 py-2.5 text-sm font-medium text-good">
          {t(lang, "outcomeSaved")}
        </p>
      )}

      <button
        onClick={save}
        disabled={saving}
        className="mt-4 inline-flex items-center gap-2 rounded-xl bg-forest px-5 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-70"
      >
        {saving && <Loader2 size={16} className="animate-spin" />}
        {t(lang, "saveOutcome")}
      </button>
    </div>
  );
}
