# Data card — every dataset and model artifact

Purpose: make the provenance of every training artifact explicit, so nobody
mistakes rule-derived labels for field measurements. This card is referenced by
`docs/ETHIOPIAN_TRAINING_PLAN.md`.

## Ground rules

1. A label is either **lab_measured** (someone with a lab sheet signed off on
   it) or **rule_derived** (a deterministic band score produced by
   `data_analysis.score_nutrient`). They are never mixed silently.
2. Advisory JSONs whose `review_status` is `unverified` are **never** training
   rows. They inform the deterministic engine only.
3. Synthetic cross-validation scores measure *rule reproduction*. They must not
   be quoted as Ethiopian field accuracy in the README, UI, API or reports.
4. The model's own prediction is never a label. Only
   `tools/import_lab_results.py` attaches verified labels.

## Datasets

| File | Rows | Label origin | Ethiopian? | Training use |
|---|---:|---|---|---|
| `Dataset/Crop_recommendation.csv` | 2,200 | public per-crop dataset | no (Indian/tropical) | crop model + `ML/crop_bands.json` |
| `Dataset/soil_data.csv` | 2,200 | rule-derived over the above (`data_analysis.py`) | no | default fertility model |
| `Dataset/soil_data_ethio.csv` (v5) | 12,000 | **sampled around zone bands, then rule-labeled from those same bands** | yes, synthetic | `SOIL_MODEL_PROFILE=agroecology` |
| `Dataset/soil_data_ethio_v6.csv` | 12,000 | same, plus stratified `soil_type` | yes, synthetic | `SOIL_MODEL_PROFILE=agroecology_v6` |
| `Dataset/soil_data_ethio_real_v1.csv` | 0 today | lab/expert verified from `db.training_samples` | yes, real | v7 (only after the row gates pass) |
| `Dataset/soil_data_ethio_v7.csv` | — | v6 synthetic + real, weighted | mixed | v7 artifact |
| `Dataset/variety_bands.json` | 10 records | literature compilation, `unverified` | yes, advisory | not used as training rows |
| `Dataset/seasonal_bands.json` | 6 crops × 2 seasons | literature compilation, `unverified` | yes, advisory | not used as training rows |
| `Dataset/soil_conditioning.json` | — | literature compilation, `unverified` | yes, advisory | not used as training rows |
| `Dataset/agroecological_zones.json` | 4 zones | EIAR/JARC/DZARC/MARC/ILRI compilation | yes | band definitions used by the generator and the engine |
| `Dataset/lime_requirement.json` | 5 soil types | EIAR/Haramaya/EthioSIS compilation | yes | lime-rate math only |
| `Dataset/ethiopian_crop_matrix.json` | 34 crops | compiled suitability bands | yes | rule-based S1/S2/S3/N only |

## v5 / v6 synthetic sets

- Generator: `tools/generate_zone_dataset.py` (`--rows 12000 --seed 40`; v6 adds
  `--soil-type`). Each CSV has a `*.meta.json` sidecar with the seed, row and
  class/zone counts and the file sha256.
- Per-row generation: draw a zone from `ZONE_WEIGHTS`, then draw N/P/K/pH from
  that zone's ideal/near/tail segments in `agroecological_zones.json`, then
  label with the same 5/3 scoring as `data_analysis.py`.
- Consequence: the label is a deterministic function of the features plus the
  zone. A model trained on this data learns the zone definitions, not
  transferable soil behaviour.

Measured on 2026-10-20 with the training feature builder and splitter:

| Evaluation | v6 | legacy `soil_data.csv` |
|---|---:|---:|
| Train accuracy | 1.000 | — |
| Stratified 5-fold CV | 0.826 | 0.974 |
| Leave-one-zone-out | 0.44 – 0.56 | n/a |

Leave-one-zone-out is the honest number for "a zone the model has never seen":
it falls back to the all-zero one-hot encoding (see
`ML/engine.py:build_soil_row`).

## Real samples (`db.training_samples`)

Flow: `POST /api/reports` with `consent_for_training: true` **and**
`TRAINING_DATA_CAPTURE=true` stores an **unlabeled** sample (`consent=1`,
`include_in_training=0`) → `tools/import_lab_results.py --apply` verifies the lab
values and attaches `Good/Moderate/Poor` → `tools/export_training_data.py`
exports only `consent=1 AND include_in_training=1 AND label_status IS NOT NULL`.

The demo DB contains no `training_samples` table until `db.init_db()` runs, and
no rows today. That is why no v7 artifact exists.

## Capture and verification (what makes a row usable)

1. `POST /api/reports` stores an **unlabeled** sample only when the request has
   `consent_for_training: true` **and** the server runs with
   `TRAINING_DATA_CAPTURE=true`. The web save panel only shows the consent box
   when `GET /api/health` returns `training_capture: true`, and it omits the
   field entirely when unticked.
2. `tools/import_lab_results.py --apply` verifies the lab values, canonicalizes
   the zone/soil type (optionally inferring the zone from a woreda lookup),
   rejects duplicate measurements, and attaches the label.
3. `tools/export_training_data.py` exports only consented, verified rows and
   adds `woreda` + `site_id` for leakage-free grouped evaluation.
4. `tools/evaluate_ethio_model.py` is the promotion gate: it refuses a CSV with
   no real rows (exit `2`), trains on a site-grouped split, and reports
   real-only metrics against the band-rule baseline. A synthetic-only run needs
   `--allow-synthetic` and prints a warning that the numbers are rule
   reproduction.

## Label semantics (Phase 4 decision)

The model returns a **zone-level Good/Moderate/Poor triage**. Per-nutrient
status, deficits and amounts come from the deterministic band engine with
precedence `variety > season > zone > crop > generic`, and remain
authoritative. A crop or variety can therefore need P while the triage still
says "Good" — that is expected, not a contradiction, and no crop/variety
context is fed to the classifier.

## Models

| Artifact | Trained on | Profile | Notes |
|---|---|---|---|
| `ML/soil_analysis.pkl` | `soil_data.csv` | default | rule-derived Indian labels |
| `ML/soil_analysis_generic.pkl` | legacy bands | `FERTILITY_PROFILE=generic` | backup |
| `ML/soil_analysis_ethio.pkl` | v5 | `agroecology` | zone one-hot |
| `ML/soil_analysis_ethio_v6.pkl` | v6 | `agroecology_v6` | + `soil_type` one-hot |
| `ML/soil_analysis_ethio_v7.pkl` | v7 CSV | `agroecology_v7` | **not built**: no verified real rows yet; gate with `tools/evaluate_ethio_model.py` |
| `ML/crop_recommendation.pkl` | `Crop_recommendation.csv` | always-on | Indian/tropical climate envelope |

Every run is logged to MLflow with the training path, dataset sha256,
`feature_cols_`, hyperparameters and metrics.

## Regenerating

```bash
python3 tools/generate_zone_dataset.py                     # v5 CSV + meta
python3 tools/generate_zone_dataset.py --soil-type \
    --out Dataset/soil_data_ethio_v6.csv                   # v6 CSV + meta
python3 ML/ml_model.py                                     # default artifact
# v5/v6 artifacts: see README "Regenerate data / retrain models"
```

Regenerating rewrites the CSV and therefore its sha256. Do not regenerate an
artifact that a published model was trained on without retraining and
re-validating it.
