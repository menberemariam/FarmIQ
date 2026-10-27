# Golden-set evaluation (Enhancement 4) — verdict and implementation

Status: **implemented** (2026-10-22). Tooling and fixtures only; no runtime,
model, dataset, API or database change.

## 0. Short answer

1. **This is not training data.** A golden set is an evaluation fixture. Using
   it as labels would contaminate the only honest measurement path and would
   encode our own assumptions as ground truth.
2. The five stress cases are useful because they target real failure modes:
   lime-first on acid Nitisols, zero/maintenance K under high native K, teff
   lodging, legume N, and Zn/B lockout at alkaline pH.
3. The paste's expected values are **not** calibrated to this repo. The golden
   cases pin the repo's authoritative engine instead:

| Paste assumption | Repo behaviour |
|---|---|
| Nitisol pH 4.8 needs 4–5 t/ha lime | `highland_nitisols` + nitisol LRF → **2.94 t/ha** with lime-first note |
| Rift K=850 is ample | Rift K band is 400–900 → **adequate**; K=950 is the ample case |
| Teff N=125 is adequate | Generic N band 60–100 → **ample**; no cereal booster is prescribed |
| Chickpea needs nodulation advice | Engine has no legume template → recorded as `known_gaps` |
| pH 8.4 must select NPSZnB | Works when `zone=rift_valley` (threshold 7.5); otherwise generic P advice |

4. Ethiopian training still requires verified lab rows. The v7 gate remains
   closed (`db.training_sample_counts()` is 0), unchanged by this work.

## 1. What was built

| File | Purpose |
|---|---|
| `tests/golden/llm_cases.json` | 50 cases (5 stress + 45 generated), 200 element assertions |
| `tests/golden/README.md` | provenance, composition and commands |
| `tools/build_golden_set.py` | deterministic builder; statuses pinned from `analyze_soil` |
| `tools/evaluate_llm_quality.py` | offline default evaluator + opt-in `--use-llm` |
| `tests/test_llm_quality.py` | schema, stress assertions, LLM fallback, metrics |
| `docs/GOLDEN_SET_PLAN.md` | this document |

### Case distribution (measured)

- 200 element statuses: 101 deficient, 81 adequate, 18 ample.
- Case-dominant: 30 deficient (60%), 17 adequate (34%), 3 ample (6%).
- 9 edge cases (18%), including exact band boundaries and 0.5-unit deficits.
- Crops include teff, wheat, maize, barley, sorghum, chickpea, onion, faba
  bean, coffee, sesame, lentil and rice; all four zones are covered.

### Contract vs quality metrics

**Gating checks** (failure exits non-zero):
- four canonical statuses match the deterministic analysis;
- no element contradicts the computed status after `merge_recommendations`;
- required/forbidden advisory phrases and numeric citations (pH lime) hold.

**Informational metrics** (reported, not gated initially):
- n-gram novelty (2-grams) of LLM prose against the deterministic template;
- English Flesch-Kincaid and average sentence length;
- words per cited metric (kg/ha, t/ha, ppm, %).

Readability is **not** computed with Flesch-Kincaid for Amharic or Afaan
Oromoo: the formula is invalid for Ethiopic script and unreliable for Qubee.
Those languages report sentence length and metric density only.

## 2. Non-regression contract

- Golden fixtures live under `tests/golden/`, never `Dataset/`.
- `tools/export_training_data.py`, `tools/import_lab_results.py`, the training
  DB, the models and `db.training_samples` are untouched.
- The evaluator writes only its optional `--json-out` report.
- `ML/engine.py`, `ML/llm.py`, `api.py` and the response schema are unchanged.
- The current **≥100 words** prompt contract stays; the paste's 60–90 word
  target is a separate product decision and would require a prompt/test change.
- No new dependency: evaluator and metrics are stdlib only.

## 3. Usage

```bash
# Deterministic gate (offline, CI-safe)
python3 tools/evaluate_llm_quality.py --verbose

# Live LLM prose (requires GROQ_API_KEY; cache disabled for the run)
python3 tools/evaluate_llm_quality.py --use-llm \
    --json-out /tmp/llm-quality.json

# Regenerate fixtures after an intentional band/override change
python3 tools/build_golden_set.py

# Tests
python3 -m unittest tests.test_llm_quality tests.test_engine tests.test_llm
```

Exit codes: `0` = at or above `--min-pass-rate` (default 1.0), `1` = below,
`2` = invalid/missing cases or missing API key for `--use-llm`.

## 4. Limitations and review policy

- Every current case is `review_status: "unverified"` and
  `label_origin: "rule_derived"`. The 50-case suite improves regression
  sensitivity; it is **not** a statistically representative sample of Ethiopian
  soils and cannot support a field-accuracy claim.
- The 45 generated cases reproduce the band engine by construction. Do not add
  them to training data and do not quote their pass rate as agronomic accuracy.
- To make a case evidence, an agronomist must verify it against EIAR/ATA/EthioSIS
  sources, set `review_status: "agronomist_verified"`, and record the source.
  Only then may it support an accuracy statement.
- The chickpea legume/nodulation case stays a documented gap until the engine
  has a legume template; it is not silently asserted as correct.
- The live LLM run is manual/nightly, never part of the fast unit suite, because
  it needs a key, network and cost.
