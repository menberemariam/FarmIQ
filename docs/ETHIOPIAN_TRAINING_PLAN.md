# Ethiopian-context training: data audit and implementation plan

Status: **implemented** (2026-10-20). Every phase below is additive and off by
default: no default artifact, dataset, response field or test was replaced.
The v7 artifact is intentionally **not** built — there are still no verified
real samples, which is a data gate, not a code gate.

### What shipped

| Phase | Change | Files |
|---|---|---|
| 0 | Data card; provenance keys in future generator meta | `docs/DATA_CARD.md`, `tools/generate_zone_dataset.py` |
| 1 | `training_capture` on `/api/health`; consent checkbox in the save panel; privacy-default tests | `api.py`, `frontend/src/lib/api.ts`, `frontend/src/components/SavePanel.tsx`, `frontend/src/i18n/strings.ts`, `tests/test_training_api.py` |
| 2 | Content dedupe, coverage report + `--require-coverage`, woreda→zone fallback | `tools/import_lab_results.py`, `tests/test_training_tools.py` |
| 3 | `woreda`/`site_id` in exports; `tools/evaluate_ethio_model.py` real-holdout gate | `tools/export_training_data.py`, `tools/evaluate_ethio_model.py`, `tests/test_training_tools.py`, `README.md` |
| 4 | Label-semantics note (model = zone triage, bands authoritative) | `README.md`, `docs/DATA_CARD.md` |
| 5 | `/api/crops` entries carry `bands_source` (non-Ethiopian baseline) | `api.py`, `frontend/src/lib/api.ts`, `README.md` |

Deliberate deviations from the original plan text, and why:

1. **Coverage gate is opt-in** (`--require-coverage`) instead of failing every
   import. Incremental imports legitimately start thin; failing by default
   would make the tool unusable and would break the existing single-row import
   tests. The report is still printed on every run.
2. **Real-holdout metrics live in the evaluator, not in `ML/ml_model.py`.**
   Logging holdout metrics from the training run would score the model on rows
   it trained on. `tools/evaluate_ethio_model.py` trains a fresh model on a
   site-grouped split and reports real-only metrics plus the band-rule
   baseline, which is what the gate needs.
3. **`/api/crops` gains one additive key** (`bands_source`) rather than a
   contract change; the 22 crop entries and their bands are untouched.

## 1. The question

> Are the Ethiopian datasets in this repo useful for training the fertility
> model in an Ethiopian context instead of the current global/Indian context —
> and if so, how do we wire that in without disturbing existing functionality?

Short answer: **yes, but only as rule distillation — not as ground truth.**
They are already wired in as opt-in artifacts (`SOIL_MODEL_PROFILE=agroecology`
/ `agroecology_v6`). The step that would genuinely make the model Ethiopian is
real, lab-verified samples, and that pipeline exists but is empty (0 rows).

The JSON-prompt text from the earlier opencode session is *inference-time
hardening*, not training data. Most of it is already in the repo
(`ML/llm.py` forces `response_format={"type": "json_object"}`, detects
`finish_reason == "length"` truncation; `ML/engine.py:parse_llm_json` strips
fences and isolates the outer object). It has no bearing on the model's
Ethiopian accuracy.

## 2. What data actually exists

| Artifact | Rows | Label origin | Ethiopian? | Role today |
|---|---:|---|---|---|
| `Dataset/Crop_recommendation.csv` | 2,200 | public per-crop dataset | **no (India/tropical)** | crop model + `ML/crop_bands.json` |
| `Dataset/soil_data.csv` | 2,200 | rule index over that data (`data_analysis.py`) | no | default fertility model |
| `Dataset/soil_data_ethio.csv` (v5) | 12,000 | **sampled from zone bands, then labeled by the same rule** | yes, **synthetic** | opt-in zone model |
| `Dataset/soil_data_ethio_v6.csv` | 12,000 | same + stratified `soil_type` | yes, **synthetic** | opt-in v6 model |
| `Dataset/variety_bands.json`, `seasonal_bands.json`, `soil_conditioning.json` | small | literature compilation, `review_status: "unverified"` | yes, advisory | **deliberately not training rows** (correct) |
| `db.training_samples` | **0** (demo DB) | lab/expert verified via `tools/import_lab_results.py` | yes, real | the only true ground truth; **empty** |

Generator: `tools/generate_zone_dataset.py` (seed 40, sha256 in the
`.meta.json` sidecars). Real-data path: `api.py:1087` capture →
`tools/import_lab_results.py` → `tools/export_training_data.py` →
`SOIL_MODEL_OUT=ML/soil_analysis_ethio_v7.pkl`.

## 3. Measured evidence (run 2026-10-20)

Same features and splitter as `ML/ml_model.py:build_features`:

| Evaluation | v6 (zone synthetic) | legacy `soil_data.csv` |
|---|---:|---:|
| Train accuracy | 1.000 | — |
| Stratified 5-fold CV | **0.826** | **0.974** |
| **Leave-one-zone-out (GroupKFold by `zone`)** | **0.44 – 0.56** | n/a |

Interpretation:

- Labels in v5/v6 are a **deterministic function of the zone bands** applied to
  the sampled N/P/K/pH. A Random Forest can only re-derive that rule; 0.83 CV
  says "the model reproduces our own thresholds", not "the model predicts
  Ethiopian fertility".
- With a zone held out entirely, the one-hot columns go all-zero and accuracy
  drops to ~50% (3 classes; chance ≈ 33%). The synthetic set therefore teaches
  **four zone definitions, not transferable soil behaviour**. It cannot
  generalize to a fifth zone, a new woreda, or a real field.
- The 0.974 on the legacy set is also rule reproduction (its labels come from
  `data_analysis.py`, not lab measurement).
- Class mix is uniform-ish across zones (Good ~47%, Moderate ~38%, Poor ~14%),
  so no zone is starved — fine for training, but it is a *designed* prior, not
  an observed one.

**Verdict**

1. Useful: as a consistency layer so a zone-aware triage agrees with the
   Ethiopian bands the deterministic engine already uses. Keep it opt-in.
2. Not useful as evidence of field accuracy; do not report its CV as an
   Ethiopian accuracy claim.
3. The real unlock is `db.training_samples`. Everything needed is coded; the
   gaps are (a) nobody can consent from the UI, (b) no lab rows exist,
   (c) validation currently cannot distinguish rule-fit from skill.
4. The crop side stays Indian-derived. Zone soil data cannot fix that, and no
   amount of v7 retraining changes `/api/crops`.

## 4. Implementation plan

Guiding rule: **every phase is additive and off by default.** No default
artifact is overwritten, no response field is removed, no schema is changed in
place. Each phase has an explicit rollback (an env var or a deleted artifact).

### Phase 0 — Data card and provenance (docs only)

Files: `docs/DATA_CARD.md` (new), `README.md` (link), `tools/generate_zone_dataset.py`
(meta keys only, applied to *future* generations — existing CSVs are not
rewritten, their sha256 stays as published).

- Record for every dataset: generator command, seed, sha256, row/class/zone
  counts, label derivation, review status, and the explicit limitation
  "synthetic labels are rule-derived; CV measures rule reproduction".
- State the rule: advisory JSONs with `review_status: "unverified"` never
  become training rows until someone signs them off.

Acceptance: only docs/meta change; no `.pkl`, `.csv` or response changes;
`python3 -m unittest tests.test_training_data tests.test_zone_model` passes.

Rollback: revert the doc.

### Phase 1 — Consent collection end-to-end (no default behavior change)

Today `consent_for_training` exists only in the API request model
(`api.py:262`); the Next.js save panel never sends it, so a real deployment
captures nothing even with `TRAINING_DATA_CAPTURE=true`.

Files:

- `api.py` — add `training_capture` (bool, read at request time) to
  `/api/health` so clients can decide whether to show the opt-in.
- `frontend/src/lib/api.ts` — extend the save payload type with optional
  `consent_for_training`.
- `frontend/src/components/SavePanel.tsx` — unchecked checkbox, rendered only
  when `/api/health` reports capture enabled; text names the purpose and that
  the sample is unlabeled and unused until lab-verified.
- `tests/test_training_api.py` — cases: capture flag off + consent → no row;
  flag on + consent absent/false → **no row** (privacy default); flag on +
  consent true → exactly one row, `consent=1`, `label_status IS NULL`,
  `include_in_training=0`.

Acceptance: existing save-report tests keep passing byte-identical payloads
when the checkbox is untouched; the report response is unchanged; capture is
still wrapped in the existing best-effort `try/except`.

Rollback: unset `TRAINING_DATA_CAPTURE` (default) — the checkbox disappears and
no row is written.

### Phase 2 — Make lab import trustworthy (tooling only)

Files: `tools/import_lab_results.py`, `tests/test_training_tools.py`.

- **Dedupe on content, not only sample hash**: reject a "new" row whose
  (N, P, K, pH, zone, soil_type) already exists with a verified label, unless
  `--allow-duplicates` (prevents the same lab sheet inflating a class).
- **Coverage gate**: print class/zone/soil_type coverage on every run; with
  `--require-coverage`, exit non-zero when any class is below
  `TRAINING_MIN_ROWS_PER_CLASS` or any zone below
  `TRAINING_MIN_ROWS_PER_ZONE` (overridable with `--min-per-class` /
  `--min-per-zone`). Opt-in so incremental imports are not blocked.
- **Optional woreda → zone fallback** (`--infer-zone-from-woreda`): only via
  `Dataset/woreda_soil_defaults.json`; unknown woreda stays `None`, never
  guessed.
- Keep `input_units=lab` conversions untouched (`ethio.n_pct_to_kg_ha`, etc.).

Acceptance: dry run remains the default; `--apply` still refuses rows without
consent; existing tests unchanged except the new cases.

Rollback: tool-only; no runtime impact.

### Phase 3 — Build v7 with honest validation (opt-in artifact)

Precondition: Phase 1 + 2 done and ≥ 300 real verified rows (≥ 30/class,
≥ 10/zone). Until then **do not produce or serve a v7 artifact** — a model
trained on 20 rows is worse than the rule.

Files: `tools/export_training_data.py` (adds `woreda` + `site_id` columns),
`tools/evaluate_ethio_model.py` (new; owns the real-holdout gate),
`tests/test_training_tools.py`. `ML/ml_model.py` needs no change — it already
honours `SOIL_GROUP_COL` / `SOIL_SAMPLE_WEIGHT_COL` and logs the source counts;
holdout metrics are measured by the evaluator instead (see deviation 2).

1. Export with spatial ids:

   ```bash
   python3 tools/export_training_data.py \
       --out Dataset/soil_data_ethio_real_v1.csv \
       --combine-with Dataset/soil_data_ethio_v6.csv \
       --combined-out Dataset/soil_data_ethio_v7.csv \
       --real-weight 1.0 --synthetic-weight 0.3 \
       --min-per-class 30 --force
   ```

   `site_id` = `woreda`/`region` for real rows, `synthetic:<zone>` for generated
   rows, so grouping never mixes a real site with synthetic rows.

2. Train, grouped by site so neighbouring samples cannot leak:

   ```bash
   SOIL_TRAIN_DATA=Dataset/soil_data_ethio_v7.csv \
   SOIL_GROUP_COL=site_id \
   SOIL_SAMPLE_WEIGHT_COL=sample_weight \
   SOIL_MODEL_OUT=ML/soil_analysis_ethio_v7.pkl \
   SOIL_MODEL_TAG=random-forest-v7-real-ethio \
   python3 ML/ml_model.py
   ```

   Note: weights are only honoured when `SOIL_SAMPLE_WEIGHT_COL` is set; the
   existing v5/v6 commands are unchanged.

3. Validate — `tools/evaluate_ethio_model.py` must report, on a **real-only**
   holdout (grouped by site): macro-F1, per-class precision/recall, per-zone
   accuracy, confusion matrix, and the **deterministic rule baseline** on the
   same rows.

   Promotion gates (all required):
   - real-holdout macro-F1 ≥ 0.65 **and** ≥ rule baseline;
   - no class with recall < 0.50 on real rows;
   - every served zone present in real training (unknown zone falls back to
     all-zero — confirm the fallback behaviour in `build_soil_row` is what we
     want);
   - `ML/soil_analysis_ethio_v7.pkl` exists and `feature_cols_` matches
     `build_soil_row` output for a v7 row (regression test).

4. Serve — `SOIL_MODEL_PROFILE=agroecology_v7` (already accepted by
   `config.soil_model_path()` and `api.py`). Default stays the legacy profile;
   a missing v7 pickle must remain a startup/degraded condition, not a silent
   fallback.

Rollback: unset the env var; delete the artifact.

### Phase 4 — Decide label semantics (needs a product decision)

Training labels use generic/zone bands, but serving advice uses
**variety > season > zone > crop > generic**. A report can therefore be
"Good" by zone bands while the crop-specific plan says P is deficient.

- Option A (recommended now): model = zone-level triage only; deterministic
  bands stay authoritative; state this explicitly in `/api/predict` docs and
  the data card.
- Option B (later): a v8 with crop/season/variety context features — a schema
  change, a new opt-in profile, and only worth it once real data covers those
  cells.

### Phase 5 — The remaining Indian-context gap (separate workstream)

`ML/crop_recommendation.pkl` and `ML/crop_bands.json` come from
`Crop_recommendation.csv`. Zone soil data does not touch them, and v7 must
**not** be trained on them for crop labels (there are none).

- `/api/crops` entries now carry `bands_source` to label the 22 per-crop bands
  as a non-Ethiopian baseline (additive key; bands unchanged).
- Collect Ethiopian crop × zone observation rows separately (yield/area), train
  a separate opt-in crop artifact later; keep the 12 Ethiopian staples
  rule-only (current, honest design) until that exists.

## 5. Guardrails (apply to every phase)

- Default profile, default datasets and all JSON contracts unchanged; new
  behaviour only behind `SOIL_MODEL_PROFILE`, `TRAINING_DATA_CAPTURE`,
  `SOIL_CONDITIONING_*` or an explicit CLI flag.
- Never use model output as a label; only `import_lab_results.py` verifies.
- Every generated CSV keeps a `*.meta.json` with sha256, seed, counts and label
  origin; every model keeps its MLflow run with data hash and `feature_cols_`.
- Synthetic CV numbers may not be quoted as Ethiopian accuracy in README, UI
  or reports.
- Run at minimum `tests.test_llm tests.test_training_api tests.test_training_tools
  tests.test_training_data tests.test_zone_model tests.test_engine` before
  merging a phase; the full 566-test suite is long-running in this environment
  (>5 min) so run it once per release, not per edit.

## 6. Recommended order

1. Phase 0 + 1 (small, immediate value: real data can start accumulating).
2. Phase 2 (quality gate before any lab sheet lands).
3. Phase 3 once the row thresholds are met.
4. Phase 4 decision (A unless someone wants crop-aware labels) — **A is now
   documented**.
5. Phase 5 as its own project: only the provenance label is done; collecting
   Ethiopian crop observations remains open.
