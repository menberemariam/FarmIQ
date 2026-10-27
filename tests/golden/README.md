# Golden advisory set (evaluation only)

`llm_cases.json` is a **regression and contract fixture**, not training data.
It is intentionally stored under `tests/`, never under `Dataset/`, and must not
be exported by `tools/export_training_data.py` or attached to
`db.training_samples`.

## What each case contains

- `input`: N/P/K/pH plus optional `crop`, `zone`, `soil_type`, `season`.
- `expected.statuses`: the four canonical statuses pinned from
  `ML.engine.analyze_soil` for the same inputs and Ethiopian zone bands.
- `expected.must_include` / `must_not_include`: advisory assertions for the five
  hand-authored stress cases (e.g. lime-first, NPSZnB, no nitrogen top-dress).
- `expected.numbers`: required numeric citations with a ±0.05 tolerance.
- `review_status`: `unverified` for every current case.
- `label_origin`: `rule_derived` for every current case.
- `known_gaps`: advisory gaps that are recorded but not gated (currently the
  chickpea legume/nodulation case).

## Composition

| Kind | Count | Purpose |
|---|---:|---|
| stress | 5 | Acid Nitisol, high-K Rift onion, teff boundary, Vertisol chickpea, alkaline Rift maize |
| generated | 45 | 4 Ethiopian zones × rotating crops/status patterns, including edge cases |
| total | 50 | 200 element assertions |

The generated cases are rule-derived regression pins. They measure whether the
serving path still reproduces the band engine; they do **not** measure field
accuracy and must not be quoted as such.

## Regenerate and evaluate

```bash
python3 tools/build_golden_set.py
python3 tools/evaluate_llm_quality.py --verbose
python3 tools/evaluate_llm_quality.py --use-llm --json-out /tmp/llm-quality.json
```

`--use-llm` requires `GROQ_API_KEY`, disables the response cache, and evaluates
the merged prose. The default deterministic run is offline and suitable for CI.

Readability is reported as English Flesch-Kincaid only; for Amharic and Afaan
Oromoo the report shows sentence length and metric density instead, because FK
is not valid for Ethiopic script.
