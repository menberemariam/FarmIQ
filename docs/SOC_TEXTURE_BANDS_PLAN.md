# SOC/texture-conditioned bands — training verdict and implementation plan

Status: **plan + one defect report. No code or artifact is changed by this
document.**

Context: Enhancement E (SOC → N, texture → K, texture + pH → P) was re-sent as
"here are the top enhancements, are these data useful to train the model in the
Ethiopian context instead of global/Indian, and how do we implement that without
affecting project functionality?"

Short answer:

1. **Enhancement E is already implemented** at the advisory (band) layer:
   `soil_conditioning.py`, `Dataset/soil_conditioning.json`, 16 passing tests,
   `GET /api/soil-conditioning`, optional API inputs and UI fields.
2. **It is not training data.** The pasted numbers are unverified rule priors,
   not measurements. Labelling rows with them reproduces the same circular
   construction as v5/v6, so any resulting accuracy number would be rule
   reproduction — measured today at **rule 0.997 vs model 0.826 CV, 0.44–0.56
   leave-one-zone-out** (`docs/DATA_CARD.md`).
3. **It is useful in three non-label roles**, all implemented additively below:
   as the **baseline the ML model must beat**, as a **feature schema for real
   rows** (the exporter currently drops `texture_class` / `soc_pct` /
   `soc_class` even though `db.training_samples` stores them), and as a
   **sampling design for real data collection** with a pre-registered check of
   the priors.
4. **Two of the five pasted rules are encoded with the wrong delta order**, so
   the shipped bands do not match the spec. Details in §2.

---

## 1. What the paste asks for vs. what exists

| Pasted item | State | Evidence |
| --- | --- | --- |
| SOC < 1% → N +20 kg/ha; SOC > 3% → N −15 kg/ha | **done** | `Dataset/soil_conditioning.json` `rules.N.soc`; applied by `soil_conditioning._rule_for("N")` before the season layer in `ML/engine._resolve_bands` |
| Clay (Vertisol) → K +40…+50; sand → K reduction | **done, wrong order** | `rules.K.texture.clay = [40, 50]` ✓; `rules.K.texture.sandy = [-40, -30]` gives **60–120**, spec says **70–110** (see §2) |
| High-clay acidic → P +20…+30 | **done** | `rules.P.texture_ph[0]`, threshold `ph_lt: 5.5` (paste's example is pH 5.0 — threshold is a judgement call, pinned by test) |
| Sand at pH 6.5 → P 12–22 | **done, wrong order** | `rules.P.texture_ph[1].delta = [-8, -3]` gives **7–27**, spec says **12–22** |
| Lookup table `(texture, soc_class) → bands`, not a formula | **done** | `Dataset/soil_conditioning.json` is exactly that table plus `defaults.soil_type_texture` / `defaults.zone_soc` |
| Woreda-level fallback | **scaffolded, empty on purpose** | `Dataset/woreda_soil_defaults.json` has `"woredas": {}`; `_woreda_entry` falls through silently |
| Qualitative names (`Koticha`, `Borebore`) accepted | **done** | `texture_classes.*.aliases`; `canonical_texture` |
| Missing SOC/texture must not break | **done** | `is_active()` requires explicit input / woreda / `SOIL_CONDITIONING_ENABLED`; with none, `_resolve_bands` is byte-identical (`tests/test_soil_conditioning.py::test_no_inputs_is_legacy_noop`) |

So the question is not "how do we add E" — it is "should E's table become
training labels". It should not (§3).

---

## 2. Defect found while checking the paste (fix first, small)

`_apply_delta` applies `delta[0]` to the lower edge and `delta[1]` to the upper
edge. A *reduction* therefore has to be written ascending (`[-30, -40]`), but
both reduction rules are written descending (min-first, as a range). Measured
with `soil_conditioning.apply_to_bands` on the spec's base bands:

```
base K 100–150, texture=sandy, delta [-40,-30]  -> [60, 120]     spec: 70–110
base P  15–30,  texture=sandy, delta [ -8, -3]  -> [ 7,  27]     spec: 12–22
```

`_apply_delta`'s `if new_lo > new_hi: swap` guard hides this: 60 < 120 and
7 < 27, so nothing looks wrong. Only the clay rule is asserted in
`tests/test_soil_conditioning.py`, so the sandy path is untested.

Corrected table values (same semantics, spec-matching output):
`rules.K.texture.sandy.delta = [-30.0, -40.0]`, `rules.P.texture_ph[1].delta =
[-3.0, -8.0]`. Everything else matches the paste once rounded to the project's
integer kg/ha bands.

---

## 3. Why the pasted numbers are not training data

1. **They are priors, not observations.** The JSON says so itself:
   `_meta.review_status: "unverified"`, `_meta.sources: ["User-supplied
   Enhancement E SOC/texture-conditioned bands (2026)"]`. No lab, no sample
   size, no confidence interval.
2. **The circularity is already measured.** `tools/generate_zone_dataset.py`
   samples around a band and labels with that same band. Result on v6: train
   1.000, CV 0.826, leave-one-zone-out **0.44–0.56**, deterministic rule
   **0.997**. A conditioning-labelled CSV would repeat this exactly: the label
   is a function of the priors, so the "accuracy" measures how well a forest
   copies a 5-row table.
3. **The band engine stays authoritative anyway.** Per `docs/DATA_CARD.md`,
   per-nutrient status, deficits and amounts come from the deterministic engine;
   the classifier only supplies a zone-level Good/Moderate/Poor triage. Training
   on E therefore adds **no serving capability** — the same deltas already run
   deterministically one call earlier.
4. **The repo already enforces this.** `tools/evaluate_ethio_model.py` exits
   `2` on a CSV with no verified real rows and has
   `--require-beats-baseline`. A conditioning-derived CSV would trip both.

Verdict: **not useful as training labels; useful as a baseline, a feature
schema, and a collection design.** Those are the phases below.

---

## 4. Where E *is* useful

### R1 — Baseline to beat (the actual test of the prior)

`evaluate_ethio_model._rule_label` scores rows with `zone_labeling_bands` only.
If real rows carry texture/SOC, the honest comparison is *model vs the
conditioned rule*, not *model vs the unconditioned rule*. This is also how a
prior earns evidence: if the conditioned rule loses to the model and to the
unconditioned rule on measured data, the priors are wrong and get corrected —
the model is not asked to launder them.

### R2 — Real-data feature schema (gap today)

`db.training_samples` stores `texture_class`, `soc_pct`, `soc_class`
(`db.py`, `tools/import_lab_results.py`), and `db.list_training_samples`
returns them, but `tools/export_training_data.py` writes only
`N,P,K,ph,zone,soil_type,woreda,site_id,Fertility_Status,source,sample_weight`,
and `ML/ml_model.build_features` never sees them. So the one place measured
SOC/texture *could* teach the model something is currently thrown away.

### R3 — Collection design (D1 addendum)

Use the 3 × 4 grid (sandy/loam/clay × very_low/low/medium/high) as the quota
plan for real samples, and pre-register the hypothesis: *does measured data show
N rising ~20 kg/ha at SOC < 1% and K bands shifting ~+45 / −35 with texture?*
That converts an unverified table into a testable claim instead of a label.

---

## 5. Implementation phases

Each phase is additive, has its own tests, and leaves every default unchanged.
`SOIL_MODEL_PROFILE` default stays `""`; `Dataset/*.csv` and `ML/*.pkl` are not
regenerated.

### E0 — Correct the two reduction deltas (advisory layer only)

- Files: `Dataset/soil_conditioning.json` (two `delta` arrays),
  `tests/test_soil_conditioning.py`.
- Change: `K sandy [-30, -40]`, `P sandy [-3, -8]`; keep the clay rule as is.
- Acceptance: new assertions pin the exact spec bands —
  `get_bands("maize", texture_class="sandy")["K"] == [base_K_lo - 30,
  base_K_hi - 40]` and `["P"] == [base_P_lo - 3, base_P_hi - 8]`; the clay
  assertions and the legacy no-op assertions still pass.
- Risk: only opt-in sandy output changes (60–120 → 70–110; 7–27 → 12–22).
  Rollback: restore the two arrays.
- Also pin the P-clay threshold: the paste says "pH 5.0 e.g.", the table uses
  `ph_lt: 5.5`. Keep 5.5 (nitisol P-fixation is material above 5.0 as well) but
  add the threshold to `_meta.review_note` so a reviewer sees it was chosen,
  not copied.

### E1 — Export the measured context (no model change)

- Files: `tools/export_training_data.py`, `tests/test_training_tools.py`.
- Change: add `texture_class`, `soc_pct`, `soc_class` to the exported column
  list and to `meta` (`texture_counts`, `soc_class_counts`); values already come
  back from `db.list_training_samples`.
- Acceptance: exported CSV carries the three columns; they are empty/NaN for
  rows that never had them; every existing column, value and meta field is
  unchanged; `--combine-with` still concatenates (the v6 base simply has no
  such columns).
- Risk: CSV schema is additive; consumers that index by name are unaffected.
  Rollback: drop the three names from the column list.

### E2 — Opt-in context features in training

- Files: `ML/ml_model.py` (`build_features`), `ML/engine.py`
  (`build_soil_row`), `tests/test_zone_model.py` or a new
  `tests/test_context_model.py`.
- Change, gated by `SOIL_TRAINING_CONTEXT=1` (default off, so no existing
  artifact or test changes):
  - `soc_pct` → one numeric feature, clipped to `[0, 10]`;
  - `soc_class` → one-hot `soc_class_<id>` with all four classes zero-padded;
  - `texture_class` → one-hot `texture_class_<id>` with all three zero-padded.
  - Serve side: `build_soil_row` already `reindex(columns=feature_cols_)`, so a
    model that declares these columns needs the same zero-padding branch that
    `zone_`/`soil_type_` already have; add it behind the same
    "model declares the column" check.
- Acceptance: default training path yields byte-identical `feature_cols_` and
  metrics; a v6 retrain with the flag off is unchanged; a tiny synthetic frame
  with context columns produces the expected padded schema; a pickled model
  with context columns scores a served row without raising.
- Risk: a mixed v7 file (real rows with context + synthetic rows without) gives
  NaN for synthetic rows; fill with class-level defaults or leave zero-padded
  and record `context_available` so the feature is not mistaken for a measured
  zero. Rollback: unset the flag.

### E3 — Conditioning-aware baseline in the promotion gate

- Files: `tools/evaluate_ethio_model.py`, `tests/test_training_tools.py`
  (or the evaluator's own tests).
- Change: `_rule_label` gains an optional conditioning pass — when the row has
  `texture_class` / `soc_pct` / `soc_class`, resolve `soil_conditioning`
  context and apply the deltas to the zone labeling bands before scoring. Report
  three ways in `--json-out` (`model`, `rule_baseline`, `conditioned_rule`)
  and compare `--require-beats-baseline` against the **stronger** of the two
  rules.
- Acceptance: a CSV without context columns produces a byte-identical report
  and exit code; with context columns the conditioned baseline differs only on
  rows where a rule matches; exit codes stay `0/1/2`.
- Risk: gate becomes stricter (compares against the strong baseline). That is
  the intent; an operator who wants the old behaviour can pass the existing
  flag combination. Rollback: one keyword default `apply_conditioning=False`.

### E4 — Prior-validation tool (turns the table into evidence)

- Files: new `tools/check_conditioning_priors.py`, tests.
- Change: reads a **real-rows** CSV (`source == "real"` required, else exit 2
  like the evaluator), buckets rows by texture × SOC class, and reports per
  cell: `n`, observed Good/Moderate/Poor mix, mean N/P/K, and the mean shift
  predicted by `Dataset/soil_conditioning.json`. JSON + human table out.
- Acceptance: with 0 real rows, exit 2 and no numbers; with a seeded fixture,
  the predicted-vs-observed deltas are printed and the JSON is stable; the tool
  never writes into `Dataset/soil_conditioning.json` (corrections stay a
  reviewed, manual diff).
- Why: this is the only path from "user-supplied prior" to "Ethiopian
  evidence", and it cannot be shortcut with a generated CSV.

### E5 — Explicitly rejected

- **No** conditioning-labelled training CSV, not even behind
  `--allow-synthetic`: the label is the rule, so the model can only copy it, and
  the deterministic engine already applies the same rule at serve time.
- **No** tuning of `Dataset/soil_conditioning.json` against synthetic CV or
  against model accuracy.
- **No** promotion of a model because it fits the conditioned rule well.

---

## 6. Non-regression contract

- `SOIL_MODEL_PROFILE` default `""`; `/api/predict`, `/api/analyze`,
  `/api/reports` payloads unchanged when no context input is supplied.
- `soil_conditioning` stays opt-in (`is_active` unchanged); unknown woreda,
  unknown texture and missing SOC keep falling through silently.
- `Dataset/soil_conditioning.json` keeps `review_status: "unverified"` until E4
  produces measured support; UI/report text keeps the "unverified" note
  (`tests/test_soil_conditioning.py` asserts it).
- `Dataset/*.csv` (except the new export columns in E1) and `ML/*.pkl` are not
  regenerated; `tests/test_zone_model.py`'s SHA assertion still holds.
- LLM cache key `(prompt, lang, model)` is untouched: conditioning enters the
  prompt only through the analysis it already receives.
- Evaluator exit codes `0/1/2` keep their meaning.
- No new dependency; E1–E4 are stdlib + pandas/sklearn already in use.

## 7. Order and effort

| Phase | Size | Blocks |
| --- | --- | --- |
| E0 delta fix | ~2 lines + tests | nothing |
| E1 export context | ~10 lines + tests | E2, E3, E4 |
| E2 context features | ~40 lines + tests | v7 training only after D1 gates |
| E3 conditioned baseline | ~50 lines + tests | honest E0/E2 evaluation |
| E4 prior validation | new tool ~120 lines | correcting the table with evidence |

E0 can ship today. E1–E2 are worth doing before the first real batch so the
measured context is captured rather than discarded. E3 must exist before any
"the model beats the rule" claim. E4 is the gate on ever removing
`"unverified"`.

## 8. Verification

```bash
# E0
python3 -m unittest tests.test_soil_conditioning -v
python3 - <<'PY'
import soil_conditioning as sc
base = {"N": [40, 80], "P": [15, 30], "K": [100, 150], "ph": [6.5, 7.5]}
out, meta = sc.apply_to_bands(base, {"texture_class": "sandy"})
print(out["K"], out["P"])     # expect [70.0, 110.0] [12.0, 22.0]
PY

# E1–E3
python3 -m py_compile tools/export_training_data.py tools/evaluate_ethio_model.py ML/ml_model.py
python3 -m unittest tests.test_training_tools tests.test_training_data \
    tests.test_zone_model tests.test_models -v

# E4 (needs real rows; exits 2 without them)
python3 tools/check_conditioning_priors.py --csv Dataset/soil_data_ethio_real_v1.csv
```

## 9. Out of scope

- Rewriting or removing the global/Indian datasets; they stay the default and
  the baseline.
- Removing the deterministic band engine in favour of a learned classifier.
- Populating `Dataset/woreda_soil_defaults.json` from anything other than
  EthioSIS / regional soil maps (still D2 in `docs/LLM_CONTRACT_PLAN.md`).
- Presenting the SOC/texture priors as official Ethiopian recommendations.
