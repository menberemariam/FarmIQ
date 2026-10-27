# LLM output contract + Ethiopian training data — verdict and implementation plan

Status: **L0–L3 and E6 implemented** (2026-10-22). The data phases remain gated
on verified rows. Every change is additive; the default model, datasets and
response contracts are unchanged.

| Phase | Shipped | Files |
|---|---|---|
| L0 | `normalize_agronomic_keys`, `coerce_element_record`; lowercase/spaced/`PH` keys now merge; unknown keys dropped | `ML/engine.py`, `tests/test_engine.py`, `tests/test_llm.py` |
| L1 | `SYSTEM_PROMPT` names the four keys, both sub-keys, no-markdown rule and truncation consequence | `ML/llm.py`, `tests/test_llm.py` |
| L2 | `parse_llm_json_partial` salvages complete records from truncated output; `LLM_MAX_TOKENS` env override | `ML/engine.py`, `ML/llm.py`, tests |
| L3 | in-process counters + `soil.llm` logger; additive `/api/health["llm_contract"]` only when `LLM_TELEMETRY=1` | `ML/llm.py`, `api.py`, `config.py`, `tests/test_api.py` |
| E6 | Ethiopian zone/soil-type context + "no non-Ethiopian products/rates" guardrail, derived from the analysis rows; `PROMPT_VERSION` bumped | `ML/engine.py`, `ML/cache.py`, `tests/test_engine.py`, `tests/test_llm.py` |

Context: the enhancement was pasted from a session about hardening the LLM JSON
pipeline ("batch multiple elements in one call"). Two questions are answered
below, then a phased plan that changes nothing that is currently default-facing.

- Q1: are the five pasted techniques useful for this feature?
- Q2: are the Ethiopian datasets useful for training the model instead of the
  global / Indian data, and how would that be implemented safely?

---

## 0. Short answer

1. The pasted material is **not training data**. It is output-contract
   engineering for the advisory layer (`ML/llm.py` + `ML/engine.py`). All five
   items are now implemented: JSON mode and the robust extractor already
   existed; L0 added case-insensitive key normalization, and L1–L3 added the
   system-prompt schema, partial salvage/telemetry and the `LLM_MAX_TOKENS`
   override.
2. It matters more in the Ethiopian deployment than in a generic one, because
   Amharic and Afaan Oromoo solutions are the longest outputs this pipeline
   ever generates, so they are the ones most likely to hit `max_tokens` and be
   discarded. It does **not** make the model Ethiopian — that comes from
   `Dataset/agroecological_zones.json`, EthioSIS bands and verified lab rows.
3. The current Ethiopian CSVs are **synthetic**: sampled around the zone bands
   and then labelled by those same bands. They prove the serving path works and
   that the feature schema is right; they cannot prove Ethiopian accuracy.
   Verified real samples today: **0** (`db.training_sample_counts()`).
   Recommendation: use them as scaffolding, keep the default profile unchanged,
   and do not enable `agroecology_v7` until the evaluator gate passes on real
   data.

---

## 1. What the pasted enhancement asks for vs. what exists

| Pasted item | State | Evidence |
| --- | --- | --- |
| 1. Four-key schema enforced in the **system** prompt | **done (L1)** | `ML/llm.py::SYSTEM_PROMPT` names the four keys, `status`/`solution`, no-markdown and truncation consequence |
| 2. Negative / "parser will reject" constraints | **done (L1/L2)** | System prompt warns truncated output is discarded; `parse_llm_json_partial` salvages only complete records |
| 3. Groq JSON mode via `model_kwargs` | **done** | `ML/llm.py`; asserted by `tests/test_llm.py::test_generation_parameters_are_locked_down` |
| 4. Robust extractor (fences, stray text, escaped braces) | **done** | `ML/engine.py::parse_llm_json`; `ML/llm.py` converts any exception to `None` |
| 5. Case-insensitive key normalizer | **done (L0)** | `ML/engine.py::normalize_agronomic_keys` keeps canonical keys, fixes casing/whitespace, drops unknown keys; called by `merge_recommendations` and the LLM parser |

Item 3's warning ("must mention JSON in the system prompt or Groq returns 400")
is already satisfied: the system message contains the word JSON.

---

## 2. Top enhancements, ranked

### E1 — Contract normalizer (P0, smallest diff, fixes a silent data loss)

**Gap.** A perfectly good LLM answer keyed `"ph"` / `"Nitrogen "` / `"nitrogen"`
is dropped with no error, log or visible signal. The report still renders, so
nobody notices the richer text was lost.

**Change.** In `ML/engine.py`:

```python
AGRO_KEY_ALIASES = {...}          # from config.ELEMENTS, lower-cased
def normalize_agronomic_keys(parsed: dict) -> dict:
    """Keep the four canonical keys, fix casing/whitespace, drop everything else."""
def coerce_element_record(value) -> dict | None:
    """Accept {"status": ..., "solution": ...}; reject non-dicts and empty solutions."""
```

Call it in two places: `get_llm_recommendations` right after `parse_llm_json`
(cache stores the normalized form) and `merge_recommendations` (defensive, for
direct callers and tests). Idempotent, no signature change.

**Tests.** `tests/test_engine.py`: lower-case keys merge as `llm`; `"PH"` maps to
`"pH"`; unknown keys dropped; non-dict element ignored; normalization is
idempotent. `tests/test_llm.py`: a lowercase Groq payload still returns a
4-key dict.

**Risk.** Alias map must be conservative — no fuzzy matching (`"nitrate"` must
not become `Nitrogen`). Rollback = revert one function and two call sites.

### E2 — Per-element salvage instead of all-or-nothing (P0)

**Gap.** `get_llm_recommendations` returns `None` if the whole JSON fails to
parse, including a response truncated after three complete elements. All four
elements then fall back to rule-based text, discarding three usable answers.

**Change.** `parse_llm_json_partial(content)` in `ML/engine.py`: try the strict
parse; on failure, scan for each canonical element key and balance-scan its
object; return the elements that parse. `get_llm_recommendations` returns that
partial dict when at least one element survives, `None` only when none do.

No change needed in `merge_recommendations`: `llm_fact_failures` already marks a
missing element as a failure and falls back per element, and the status/N5
numeric gates still apply to each recovered element.

**Tests.** Fenced + truncated payload recovers 3 of 4 and merges 3 `llm` + 1
`rule-based`; garbage recovers nothing and still returns `None`.

**Risk.** A partially parsed record could carry a truncated `solution` — the
existing N5 rule (solution must restate a computed fact within 5 %) filters it.
Rollback = drop the fallback branch and return `None` as today.

### E3 — Enforce the schema where Llama actually obeys it (P1)

**Gap.** Constraints live in the user prompt next to the data. The pasted note is
right that the system message carries more weight, and the system message here
is generic.

**Change.** Hoist a `SYSTEM_PROMPT` constant into `ML/llm.py` naming the four
keys, the two sub-keys, the "no markdown, no preamble" rule, and the
"truncated output is discarded" consequence. Keep the JSON schema and
"minimum 100 words" in `build_prompt` — `tests/test_engine.py` asserts those
strings there, and the localized-language instructions belong with the schema.

**Tests.** Extend the existing `test_generation_parameters_are_locked_down` to
assert the system message contains all four key names and the word JSON (Groq
requirement). No change to parsed output.

**Risk.** Prompt-only; rollback is a one-line string revert. Do not move the
word-count or band text out of `build_prompt` or existing tests break.

### E4 — Truncation: measure, then widen the budget once (P1)

**Gap.** `LLM_MAX_TOKENS = 1024` for four solutions of ≥100 words. In Amharic and
Afaan Oromoo that budget is tight, and a truncated response is detected
(`finish_reason == "length"`) and thrown away — so the tokens are paid for and
the output is discarded. E2 recovers the complete elements first.

**Change, in order.**
1. Ship E2 — most truncations then cost one element, not four.
2. Count truncations (E5). If the rate is material, raise the default to 1536
   and allow `LLM_MAX_TOKENS` to override. A higher cap costs nothing on
   non-truncated responses (generation stops at the stop token); it only pays
   for tokens that today are generated and discarded. The 8 s hard timeout
   (`LLM_TIMEOUT_SECONDS`) still bounds latency.
3. Optional retry only when `finish_reason == "length"`, behind
   `LLM_RETRY_ON_TRUNCATION` (default off), because a retry doubles latency and
   cost on exactly the slowest path.

**Tests.** Update the single `max_tokens` assertion if the default changes; add
a test that `LLM_MAX_TOKENS` from the environment is respected; a truncation
test that still returns `None` when nothing is recoverable.

**Risk.** The one intentional default change in this plan. Rollback:
`LLM_MAX_TOKENS=1024`.

### E5 — Contract telemetry (P2)

**Gap.** There is no counter anywhere: parse failures, truncations, rejected
elements and fallbacks are all invisible. `api.py` and `ML/` use no logging
today.

**Change.** `logging.getLogger("soil.llm")` in `ML/llm.py`; module-level
counters (`llm_calls`, `llm_parse_failures`, `llm_truncations`,
`llm_elements_recovered`, `llm_contract_rejects`) with one structured log line
per call; expose them additively in `/api/health` under `llm_contract` when
`LLM_TELEMETRY=1`.

**Tests.** A `mock` Groq failure increments the counter; `/api/health` without
the flag is byte-identical to today.

### E6 — Ethiopian grounding in the prompt (P2) — **implemented**

**Gap.** The prompt carries EthioSIS bands, the crop and the real product
nutrient contents (good), but not the zone or soil type, and it never tells the
model that product names/rates from other regions are unacceptable.

**Change.** `build_prompt` reads `zone` / `soil_type` from the analysis rows
(no signature or caller change) and appends one guardrail line: "Use only the
products and nutrient contents listed above; do not substitute products or
application rates used outside Ethiopia." A zoneless analysis produces the
previous prompt byte for byte. `PROMPT_VERSION` is `2026-10-22.1` so cached
prose cannot answer under the old prompt.

**Tests.** A zoned prompt contains the zone and the guardrail; a zoneless prompt
is unchanged.

**Why this is a guardrail and not a model change.** The LLM never decides the
verdict. `merge_recommendations` rejects any element whose status contradicts
the computed status or whose numbers do not restate a computed fact, so
region-foreign advice cannot become the recommendation — only the prose.

---

### E5 runbook — measure before changing the budget (no code)

The counters are inert unless the deployment sets `LLM_TELEMETRY=1`; without
the flag `/api/health` is byte-identical to the previous payload.

1. Enable `LLM_TELEMETRY=1` and read `GET /api/health["llm_contract"]`.
2. Use `llm_calls` as the denominator:
   - `llm_parse_failures` — whole response unusable.
   - `llm_truncations` — `finish_reason == "length"`.
   - `llm_elements_recovered` — complete records salvaged from truncation.
   - `llm_contract_rejects` — unknown/malformed keys dropped by the normalizer.
3. Decision rules:
   - `llm_truncations / llm_calls > 0.05` → set `LLM_MAX_TOKENS=1536` (no code)
     and re-measure. Do not raise it further without evidence; Amharic and
     Afaan Oromoo are the longest outputs and therefore the first to truncate.
   - High `llm_contract_rejects` with low `llm_parse_failures` → schema drift;
     check `SYSTEM_PROMPT` and the Groq model name before editing the parser.
   - High `llm_parse_failures` → inspect the failure mode; do not add retries by
     default.
4. Retries stay off. A retry doubles cost and latency on the slowest path and
   must be a separate, explicit opt-in change.

No prompt text, soil value, or API key is exposed by these counters.

## 3. Are the Ethiopian data useful for training instead of global / Indian?

| Dataset | Rows | Label origin | Useful for the Ethiopian model? |
| --- | --- | --- | --- |
| `Dataset/soil_data.csv` (legacy) | 2,200 | synthetic, generic bands; no `.meta.json` | Serves the **default** profile; keep as fallback. Not Ethiopian. |
| `Dataset/soil_data_ethio.csv` (v5) | 12,000 | sampled around zone bands, labelled by the same bands | Useful as **scaffolding** (zones, schema, serving path). Not evidence. |
| `Dataset/soil_data_ethio_v6.csv` | 12,000 | same + stratified `soil_type` | Same. Best structural test bed. |
| `Dataset/Crop_recommendation.csv` | 2,200 | Indian source | Baseline only; already labelled `bands_source: "Crop_recommendation.csv (non-Ethiopian baseline)"` on `/api/crops`. |
| Training DB (consented reports) | 0 verified | real users + lab | The only data that can justify accuracy claims. |

Measured, reproducible with the repo's own code:

- v6 stratified CV accuracy **0.826**; in-sample accuracy of the shipped v6
  artifact **0.984** (re-measured on `ML/soil_analysis_ethio_v6.pkl`).
- Leave-one-zone-out **0.44–0.56** — the number that matters for a zone model.
- The deterministic band rule scores **0.997** on the same rows, because the
  labels *are* the rule. The model is not learning Ethiopian soil behaviour; it
  is approximating the labelling function.
- Legacy global model **0.974** on its own synthetic rows, same caveat.

Verdict: **useful to build and wire the Ethiopian feature; not useful as
evidence that the model is better than the Indian/global one.** A 12,000-row
CSV whose labels come from the same bands that generated the features cannot
outperform the bands. Swapping the default profile to `agroecology_v6` on the
strength of 0.826 would be a false claim. That is exactly why v7 requires real
rows and `tools/evaluate_ethio_model.py` refuses a synthetic-only CSV (exit 2)
unless `--allow-synthetic` is passed.

---

## 4. Implementation phases

Each phase is additive, has its own tests, and leaves every default unchanged.

### L0 — Contract normalizer (E1)

- Files: `ML/engine.py` (two functions, two call sites), `tests/test_engine.py`,
  `tests/test_llm.py`.
- Acceptance: lowercase / spaced / `PH` payloads produce `source: "llm"` for
  the affected elements; unknown keys never reach `merge_recommendations`;
  `tests.test_engine` and `tests.test_llm` pass unchanged plus the new cases.
- Rollback: revert the two calls; functions are inert when unused.

### L1 — System-prompt schema (E3)

- Files: `ML/llm.py`, `tests/test_llm.py`.
- Acceptance: system message names all four keys, both sub-keys, no-preamble
  rule, truncation consequence, and still contains "JSON"; parsing unchanged.
- Rollback: restore the previous three-line system message.

### L2 — Partial salvage + budget (E2, E4)

- Files: `ML/engine.py` (`parse_llm_json_partial`), `ML/llm.py` (use it,
  `LLM_MAX_TOKENS` env override), `tests/test_engine.py`, `tests/test_llm.py`.
- Acceptance: 3-of-4 truncated payload merges 3 LLM + 1 rule-based; all-garbage
  still returns `None`; per-element N5 and status gates still reject bad rows;
  `LLM_MAX_TOKENS` env is honoured.
- Rollback: delete the fallback branch; behaviour returns to all-or-nothing.

### L3 — Telemetry (E5)

- Files: `ML/llm.py`, `api.py`, `tests/test_llm.py`, `tests/test_api.py`.
- Acceptance: counters move on each failure class; `/api/health` unchanged
  unless `LLM_TELEMETRY=1`, and additive then.

### D1 — Real Ethiopian samples (data, no code)

- Target: ≥1,200 verified rows, ≥4 zones, ≥2 seasons, ≥2 labs; ≥150 rows per
  fertility class; `label_source = lab_measured`; consent captured per sample.
- Pipeline already built: consent checkbox → `db.save_training_sample` →
  `tools/import_lab_results.py --apply` (content dedupe, coverage report,
  `--infer-zone-from-woreda`) → `tools/export_training_data.py`
  (`site_id`, `woreda`, real-only rows).
- Acceptance: `db.training_sample_counts()["included"] >= 1200` and
  `by_label_source` dominated by `lab_measured`.

### D2 — Woreda defaults (data, no code)

- `Dataset/woreda_soil_defaults.json` is empty and marked `unverified` on
  purpose. Populate only from EthioSIS / regional soil maps, with `_meta.sources`
  filled. Unknown woreda must continue to fall through silently.
- Acceptance: one populated woreda resolves; unknown woreda unchanged; a test
  pins both.

### D3 — Ethiopian crop context (data)

- `Dataset/ethiopian_crop_matrix.json` exists; `/api/crops` now labels each
  entry's `bands_source`. Next step is sourcing Ethiopian trial/recommendation
  data (EIAR / ATA / regional extension) per crop, adding a `source` field per
  entry, and keeping the Indian matrix as the baseline fallback.
- Acceptance: every `/api/crops` entry states its provenance; no numeric band
  changes until a verified source replaces one, and that change is one entry at
  a time with a test.

### D4 — v7 build and promotion gate

```bash
# 1. real rows only (default --out is Dataset/soil_data_ethio_real_v1.csv)
python3 tools/export_training_data.py --min-per-class 150 --force

# 2. gate on real rows before any synthetic rows are mixed in
python3 tools/evaluate_ethio_model.py \
    --csv Dataset/soil_data_ethio_real_v1.csv \
    --leave-one-zone-out --min-macro-f1 0.65 --require-beats-baseline \
    --json-out /tmp/v7-report.json

# 3. only if step 2 exits 0: combine real rows with the v6 scaffold
python3 tools/export_training_data.py \
    --combine-with Dataset/soil_data_ethio_v6.csv \
    --combined-out Dataset/soil_data_ethio_v7.csv \
    --real-weight 3.0 --force

# 4. train; site_id keeps woreda/region groups out of the test fold
SOIL_TRAIN_DATA=Dataset/soil_data_ethio_v7.csv \
SOIL_MODEL_OUT=ML/soil_analysis_ethio_v7.pkl \
SOIL_MODEL_TAG=random-forest-v7-real-ethio \
SOIL_GROUP_COL=site_id python3 ML/ml_model.py
```

`TRAINING_REAL_CSV` is `Dataset/soil_data_ethio_real_v1.csv`, not a v7 name; the
v7 file is only produced by `--combined-out` in step 3, and only after the
real-only gate passes. If some exported rows have no zone, add
`--allow-missing-zone` to step 1 rather than dropping them silently.

- Acceptance: step 2 exits 0, i.e. real-holdout macro-F1 ≥ 0.65 **and** at or
  above the band rule, with the leave-one-zone-out spread reported; the existing
  suite passes with the v7 artifact present; the default profile is untouched.
  The `--real-weight` in step 3 is a starting point, not a tuned value — pick it
  from the evaluator, never from the synthetic CV.
- Rollback: the artifact is opt-in via `SOIL_MODEL_PROFILE=agroecology_v7`;
  deleting the pickle returns the system to today's behaviour.

---

## 5. Non-regression contract

- `SOIL_MODEL_PROFILE` default stays `""` (legacy model). No v5/v6/v7 switch.
- `Dataset/*.csv` and `ML/*.pkl` are not regenerated: `tests/test_zone_model.py`
  asserts the dataset SHA against its `.meta.json`.
- `parse_llm_json` keeps raising `ValueError`; `get_llm_recommendations` keeps
  its "never raises, silent fallback" contract.
- `merge_recommendations` keeps its output shape, ordering and
  `source: "llm" | "rule-based"` values.
- `build_prompt` gains only keyword arguments with defaults; positional callers
  are unaffected.
- No new dependency: the normalizer and salvage parser are stdlib.
- Cache key (`prompt`, `lang`, `model`) is unchanged, so warmed caches stay valid.

## 6. Explicitly out of scope

- No removal or rewrite of the global/Indian datasets; they remain the default
  and the baseline.
- No fuzzy key matching, no "repair" of malformed `solution` text — rejected
  elements fall back deterministically.
- No second LLM call per report by default (cost/latency).
- No accuracy claim from synthetic rows, in docs, UI or commit messages.

## 7. Verification for whichever phase is implemented

```bash
python3 -m py_compile ML/engine.py ML/llm.py api.py
python3 -m unittest tests.test_engine tests.test_llm tests.test_llm_cache -v
python3 -m unittest tests.test_api tests.test_training_api tests.test_training_tools -v
```

## 8. Streaming endpoint

The asynchronous layer (`ML.llm.stream_llm_recommendations` and
`POST /api/predict/stream`) reuses the same prompt, cache, parser and
`merge_recommendations` validation as the synchronous path. It streams only
complete element records after validation; the deterministic snapshot and the
final `done` payload keep the client usable when the LLM is absent, slow or
returns malformed JSON.

- `LLM_STREAM_TIMEOUT_SECONDS` (default 30) bounds the whole stream;
  `LLM_STREAM_IDLE_SECONDS` (default 8) bounds the gap between chunks.
- Timeouts, idle stalls and transport errors are not cached; clean and
  length-truncated completions may be.
- `NEXT_PUBLIC_LLM_STREAMING=1` enables the frontend consumer; the default is
  the unchanged non-streaming request.
- Streamed text is inference output. It is never a training row or label, and
  prompts/completions are not logged by default.
