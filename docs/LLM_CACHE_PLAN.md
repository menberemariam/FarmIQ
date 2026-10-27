# LLM recommendation cache (Enhancement 1) — training verdict and plan

Status: **verdict + plan only. No code or artifact is changed by this
document.**

Context: Enhancement 1 (cache identical analyses) was re-sent as "are these data
useful to train the model in the Ethiopian context instead of global/Indian, and
how do we implement that without affecting project functionality?"

Short answer:

1. **This enhancement contains no data.** It caches LLM prose keyed by prompt;
   nothing in it is a soil observation, a label, or a feature.
2. **It is already implemented**, and more strictly than the paste asks:
   `ML/cache.py` (TTL + LRU + in-flight single-flight + deep copies + env
   flags), `ML/llm.py` call site, `tests/test_llm_cache.py` (6 tests),
   `tests/test_llm.py` (clears state per test). 42 engine/LLM tests pass.
3. **One suggestion in the paste would be a regression** if adopted: the
   `(N, P, K, ph, crop, lang)` primitive key collides across Ethiopian context.
   The prompt-hash key that ships today is correct; keep it.
4. **It cannot train the fertility model.** Distilling cached LLM text as
   labels would add a third circular layer on top of v5/v6 (§3).
5. There is a short, additive list of improvements worth doing (telemetry,
   documented multi-process limitation, optional review workflow) — §4.

---

## 1. Paste vs. what exists

| Pasted item | State | Evidence |
| --- | --- | --- |
| Manual `OrderedDict` + `threading.Lock` instead of `lru_cache` | **done** | `ML/cache.py::LLMRecommendationCache` (`OrderedDict`, `RLock`) |
| LRU order maintained on hit and put | **done** | `get()`/`put()` `move_to_end`; `popitem(last=False)` |
| Bounded size, evict oldest | **done** | `maxsize` from `LLM_CACHE_MAXSIZE` (default 128) |
| `None` failures never cached ("Rule 2") | **done** | `put()` returns early on `value is None`; `test_none_is_never_cached` asserts compute runs twice |
| Exclude `api_key`/`timeout` from the key | **done, by construction** | neither appears in the prompt; key = `sha256(PROMPT_VERSION, model, lang, prompt)` |
| Disable cache from tests | **done** | `enabled` property; `tests/test_llm.py` clears + re-enables in `setUp`/`tearDown` |
| Cache-clear fixture (pytest `autouse`) | **done differently** | suite is `unittest`; equivalent `setUp`/`tearDown` in `tests/test_llm.py` |
| Thread safety without duplicate execution | **exceeded** | `get_or_compute` keeps an `_inflight` `Future` map: concurrent misses compute once (`test_concurrent_misses_compute_once`, 4 threads → 1 call) |
| `lru_cache` is thread-safe but can run twice | accurate | that is exactly the gap `_inflight` closes here |
| Extra: TTL | **done, not in paste** | `LLM_CACHE_TTL_SECONDS` (default 86400), expired entries dropped on read |
| Extra: mutation safety | **done, not in paste** | `deepcopy` on both `put` and `get`; `test_hit_returns_a_copy` |
| Extra: prompt-version invalidation | **done, not in paste** | `PROMPT_VERSION = "2026-10-20.2"` in the key |

---

## 2. Do not adopt the primitive key

The paste builds the key from `(N, P, K, ph, crop, lang)`. In this project that
collides on every Ethiopian context field that changes the advice:

- `zone`: `highland_nitisols` vs `highland_vertisols` have different ideal
  bands, so the same N/P/K/pH yields different deficits and different text.
- `variety` / `region`: the variety layer outranks zone and crop.
- `season`: meher/belg rewrites the N band.
- `texture_class` / `soc_pct` / `soc_class` / `woreda`: Enhancement E shifts
  N/K/P bands before the prompt is built.
- product `selection`: changes the nutrient-contents line the LLM must restate.

A primitive key would serve a Vertisol farmer the nitisol report — a silent
cross-context leak, and one the current tests already guard
(`test_key_includes_zone_and_soil_type`). The prompt hash is strictly safer and
costs nothing: `build_prompt` is deterministic for the same context.

Also keep `PROMPT_VERSION`: without it, changing `build_prompt` would leave old
prose answering under a new contract. Bump it whenever the prompt's meaning
changes, never for formatting-only edits.

---

## 3. Why this is not training data

1. **The cached value is prose, not a measurement.** It is the LLM's four
   `{status, solution}` records for one prompt. No lab value, no row, no label
   class.
2. **It is rule-constrained twice.** `build_prompt` feeds the computed bands and
   deficits; `merge_recommendations` rejects any element whose status contradicts
   the computed status or whose numbers do not restate a computed fact (N5).
   Training on the accepted text teaches the model to reproduce the band
   engine's own output.
3. **That is the same trap already measured.** `docs/DATA_CARD.md`: v6 train
   1.000, CV 0.826, leave-one-zone-out 0.44–0.56, rule 0.997. A cache-derived
   label set would be a *third* generation of rule-derived labels, with less
   information than v6 (the LLM sees only four numbers plus context, and its
   text is filtered back toward the rule).
4. **The promotion gate would refuse it.** `tools/evaluate_ethio_model.py` exits
   `2` without verified real rows; cached prose can never supply them.

Verdict: caching is a **cost/latency and reproducibility** feature. It is not a
substitute for Ethiopian training data, and it must never write into a training
CSV. No accuracy claim can come from it.

---

## 4. Additive improvements worth planning

### C1 — Cache telemetry (P2, smallest useful change)

- Files: `ML/cache.py`, `api.py`, `tests/test_llm_cache.py`,
  `tests/test_api.py`.
- Change: monotonic counters on the singleton — `hits`, `misses`, `inflight`
  (waiters that joined), `evictions`, `expired`, `rejected_none` — exposed
  additively as `/api/health["llm_cache"]` only when `LLM_CACHE_TELEMETRY=1`.
- Why: hit rate is the only way to know whether the 128-entry/24 h defaults are
  right for real traffic, and `rejected_none` distinguishes "no key" from
  "Groq failing" without logging prompt text.
- Acceptance: `/api/health` without the flag is byte-identical to today; a
  simulated hit/miss moves the counters; no prompt or API key is ever exposed.
- Rollback: delete the block; counters are inert when the flag is off.

### C2 — Documents the real deployment limitation (P2, docs only)

- The cache is **per process**. On a multi-worker host (Railway/Gunicorn) each
  worker warms its own copy, so hit rate scales with `1/workers` and a restart
  clears it. That is acceptable for an optional enrichment call, but it must be
  stated in `README.md` next to `LLM_CACHE_*` so nobody reads a low local hit
  rate as a broken cache.
- A shared cache (Redis/Memcached) would fix it but adds a service and a
  dependency; not justified while the LLM call is optional and bounded by an 8 s
  timeout. Out of scope until there is traffic data from C1.

### C3 — Bounded wait for in-flight joins (P3, defensive)

- `get_or_compute` waiters call `future.result()` with no timeout. The owner is
  itself bounded (`LLM_TIMEOUT_SECONDS` + `ThreadPoolExecutor` in
  `ML/llm.py`), so this is theoretical, but a deadline derived from the same
  timeout would make the guarantee explicit.
- Acceptance: an owner that raises/returns still resolves the future; waiters
  never hang past the deadline; existing concurrency test unchanged.

### C4 — Agronomist review queue (optional, evidence path — not training)

- If Ethiopia-specific advice quality is the actual goal, the honest route is a
  human reviewing cached entries per zone/crop and scoring them; those human
  scores are labels a model could one day learn from. This requires a storage
  and consent decision (the prompt contains field values), so it stays a
  documented option, not a phase.
- Explicitly **not** implemented by exporting cache to CSV and training on it.

---

## 5. Non-regression contract

- Key stays `sha256(PROMPT_VERSION, model, lang, prompt)` — no primitive keys.
- `None` is never stored; `get_or_compute` still computes exactly once per key
  under concurrency.
- `get`/`put` keep returning independent copies (no shared mutable state).
- `LLM_CACHE_ENABLED` default `true`, `LLM_CACHE_MAXSIZE=128`,
  `LLM_CACHE_TTL_SECONDS=86400` unchanged.
- `/api/health` payload unchanged unless `LLM_CACHE_TELEMETRY=1`, and even then
  additive only; `training_capture` and `model_version` keep their positions
  semantically (clients read by name).
- `tests/test_llm.py` and `tests/test_engine.py` are not modified by C1–C3.
- No new dependency; no prompt text, API key or soil value is ever logged or
  exposed by telemetry.

## 6. Verification

```bash
python3 -m py_compile ML/cache.py ML/llm.py api.py
python3 -m unittest tests.test_llm_cache tests.test_llm tests.test_engine -v
python3 -m unittest tests.test_api -v          # /api/health unchanged
```

## 7. Out of scope

- Any training use of cached LLM output, including distillation, weak
  supervision, or "generate the Ethiopian dataset with the LLM".
- A primitive `(N,P,K,ph,crop,lang)` key.
- An external/shared cache service before C1 shows the local hit rate is
  material.
- Persisting prompts (they contain field values) to disk.
