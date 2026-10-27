# 🌱 Soil Analysis

A Python project that predicts soil fertility and provides AI-driven improvement
recommendations for better crop yield.
It combines **Random Forest classification** with a **hybrid recommendation engine**
(deterministic agronomy calculations + optional LLM enrichment), adds **crop
recommendation**, **crop-specific nutrient targets**, **per-plot report history
with improvement tracking** and a **REST API** for external UIs.

Live Project link: https://huggingface.co/spaces/pymit/Soil-Analysis

---

## **Features**

- Predict soil fertility (**Good / Moderate / Poor**) using **Random Forest** trained on N, P, K, pH.
- **Crop-specific nutrient targets** — select a crop and the analysis switches to its real
  ideal bands derived from the dataset (e.g. rice needs N 76–84 kg/ha, apple only 18–27),
  with recommendations and amounts calculated against *those* bands.
- Per-nutrient analysis against fixed generic ideal bands (N 40–60, P 15–30, K 100–150, pH 6.0–7.0).
- **Deterministic recommendations with calculated amounts** — kg/ha derived from your
  measured deficit and real fertilizer nutrient contents (urea 46% N, DAP 46% P₂O₅,
  potassium sulfate 50% K₂O, lime/sulfur rates by pH gap). Works **offline, no API key needed**.
- **Optional LLM enrichment** (Groq, or a local Ollama server) when configured: the
  AI's answer is validated against the computed deficit per element and automatically
  replaced by the deterministic calculation if it contradicts it. Streamlit does this
  automatically; the REST API exposes it per request via `use_llm` on
  `POST /api/predict`.
- **Crop recommendation**: top-3 suitable crops for your soil + climate (22 crops,
  ~99% CV accuracy).
- **Report history & improvement tracking**: save every prediction to SQLite per plot/field;
  re-test after a season and see real before/after deltas (N +20, P +8, pH +0.9...) and
  fertility class changes, plus a nutrient trend chart.
- **🌐 Multilingual UI** — English, Amharic languages.
- **Agent & Farmer Portal**: register farmers, manage their plots (size, GPS,
  previous crop) and follow every soil test per farmer, with yield-outcome
  follow-up (expected vs actual harvest). Seed a realistic demo in one command:
  `python3 tools/seed_demo.py`.
- **Simulated IoT pipeline**: a device-facing ingest contract
  (`POST /api/iot/readings`, token-authenticated), device registry with
  online/offline status, quality flags (stale timestamps, sudden value jumps),
  a live **IoT dashboard** (`/iot`, refreshes every 5s) with trend charts and a
  per-device live assessment, plus an in-process simulator and an external CLI
  tool (`tools/iot_simulator.py`) sharing one generator. Values are
  **simulated** and labeled as such everywhere.
- **IoT → Farmer advisories (alerts)**: every reading is checked against
  documented agronomic thresholds (moisture, pH, N, temperature, battery,
  data quality) — one active alert per condition, auto-resolved when the
  condition clears, acknowledgeable by an agent with a deep link to the farmer
  profile. Site-wide bell badge + alert panel on `/iot`. Optional notification
  e-mail (env-gated, **off by default** — works fully offline).
- **One-click "Run Demo"** (offline, reproducible): backfills a believable
  reading history for a chosen scenario, runs the full quality + advisory
  pipeline, and refreshes the dashboard.
- **CSV export** of a device's readings (chronological, spreadsheet-ready).
- **Fertilizer cost estimate** per saved report (`GET /api/reports/{id}/cost-estimate`):
  turns the calculated kg/ha amounts into product quantities with ETB subtotals
  using **indicative demo prices** (seeded `fertilizer_prices` table, editable
  per deployment) — clearly labeled as estimates, never as quotations.
- **Printable report / PDF export**: each report in the agent portal has a
  *Print* button opening a dedicated print layout (`/print/{reportId}`) —
  identity, sample values, action plan, top crops, cost table — exported via
  the browser's print-to-PDF (zero server-side PDF dependency).
- **REST API** (`/api/predict`, `/api/crops`, `/api/reports`, `/api/farmers`,
  `/api/plots`, `/api/reports/{id}/outcome`, `/api/iot/*`) — ready for the
  React/Next.js UI.
- Warnings when inputs fall outside the training data range (no silent extrapolation).
- Cross-platform (`pathlib` paths), 566 unit tests.

---

## **Installation**

1. Clone the repository:
```bash
git clone https://github.com/mitanshhh/Soil-Analysis.git
```

2. Create and activate a virtual environment:
```
python -m venv venv
source venv/bin/activate  # Linux / macOS
venv\Scripts\activate     # Windows
```

3. Install required dependencies:
```
pip install -r requirements.txt
```

4. (Optional) Create a `.env` file and add your API key for
   [ChatGroq](https://console.groq.com/keys) to enable the AI-enriched recommendations:
```
GROQ_API_KEY=your_api_key_here
```
Without a key the app runs fully offline with calculated recommendations.

Optional LLM controls: `LLM_TIMEOUT_SECONDS` (default 8), `LLM_MAX_TOKENS`
(default 1024), `LLM_CACHE_ENABLED` / `LLM_CACHE_MAXSIZE` /
`LLM_CACHE_TTL_SECONDS` for the in-process prompt cache, and
`LLM_TELEMETRY=1` to add contract counters under `llm_contract` on
`GET /api/health`. Truncated responses keep the complete element records and
fall back per missing element.

To run the advisory layer fully locally instead of Groq:

```bash
ollama pull llama3.2
export LLM_BACKEND=ollama
export OLLAMA_MODEL=llama3.2:3b        # default
export OLLAMA_BASE_URL=http://localhost:11434
```

No `GROQ_API_KEY` is needed in this mode. Groq remains the default backend;
`LLM_BACKEND=ollama` is opt-in. A small local model is weaker at strict JSON
and Amharic/Afaan Oromoo prose, so the existing per-element validation and the
deterministic fallback remain active. On an 8 GB machine `llama3.2:3b` is the
safe choice; 7B quantized variants are borderline.

### Stream validated advice (SSE)

`POST /api/predict/stream` accepts the same `PredictRequest` body as
`POST /api/predict` and returns `text/event-stream`. The existing endpoint is
unchanged; this one is additive.

```bash
curl -N http://localhost:8000/api/predict/stream \
  -H 'Content-Type: application/json' \
  -d '{"N":120,"P":30,"K":200,"ph":6.2,"crop":"maize","use_llm":true}'
```

Events:

```
event: snapshot
data: {full deterministic PredictResponse}

event: element
data: {one validated Recommendation, source: "llm"}

event: done
data: {recommendations, llm_used, validated, rejected, truncated, reason}

event: error
data: {message, reason}          # only on a fatal stream failure
```

Only complete records that pass the same status and numeric-fact validation as
`/api/predict` are sent as `element` events. The final `done` event always
carries a full recommendation list, falling back to the deterministic engine
for rejected or missing elements. `LLM_STREAM_TIMEOUT_SECONDS` (default 30)
caps the whole stream and `LLM_STREAM_IDLE_SECONDS` (default 8) aborts a
stalled connection while keeping already-recovered elements. Timeouts and
transport errors are not cached.

The Next.js panel consumes the endpoint only when
`NEXT_PUBLIC_LLM_STREAMING=1` is set at build time; otherwise it keeps using
`POST /api/predict`. If streaming fails before the snapshot arrives, the client
falls back to the non-streaming endpoint automatically.

Streamed chunks are model output, **not** verified training data. Never write
them to `db.training_samples` or the training CSVs; only
`tools/import_lab_results.py` promotes lab-verified rows.

### Evaluate advisory quality (golden set)

`tests/golden/llm_cases.json` is an **evaluation-only** set of 50 Ethiopian
cases (200 element assertions): 5 hand-authored stress cases plus 45 generated
regression cases. It is never exported as training data.

```bash
python3 tools/evaluate_llm_quality.py --verbose          # offline, CI-safe
python3 tools/evaluate_llm_quality.py --use-llm \
    --json-out /tmp/llm-quality.json                     # needs GROQ_API_KEY
```

The deterministic run gates pinned statuses, required/forbidden advisory
phrases and numeric citations. It also reports n-gram novelty, readability and
words-per-metric informationally. Readability uses English Flesch-Kincaid only;
Amharic/Afaan Oromoo report sentence length and metric density instead. See
`docs/GOLDEN_SET_PLAN.md` and `tests/golden/README.md`; regenerate fixtures with
`python3 tools/build_golden_set.py`.

## Usage

Run the Streamlit app:

```
streamlit run main.py
```

- Pick a language in the sidebar, optionally select your **target crop**.
- Enter Nitrogen, Phosphorus, Potassium, and pH values for your soil.
- Click **Predict Fertility**.
- View the predicted fertility status and per-element recommendations.
- Name your plot in the sidebar and click **Save This Report**.
- Open **📚 Soil Report History** to compare seasons (deltas + trend chart).
- Expand **"🌾 Recommend a Crop for This Soil"** for the top-3 crops.

## REST API

Start the API:

```
uvicorn api:app --host 0.0.0.0 --port 8000
```

Interactive docs at http://localhost:8000/docs

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Service status |
| `/api/crops` | GET | All 22 crops with their real nutrient bands, scientific names, climate medians |
| `/api/crops/ethiopian` | GET | Ethiopian crop matrix (34 crops): S1/S2/N bands for pH/temp/rainfall, Amharic names, regions |
| `/api/crops/suitability` | POST | Rule-based land-suitability classes (S1/S2/S3/N) for the 12 staples or any matrix crop |
| `/api/varieties` | GET | Ethiopian crop × production-area × variety target bands (optional `crop`/`region` filters) |
| `/api/seasons` | GET | Meher/Belg seasonal N bands + crop/season reference rows (optional `crop` filter) |
| `/api/soil-conditioning` | GET | SOC/texture conditioning classes, rules and metadata (Enhancement E) |
| `/api/predict` | POST | Fertility status + confidence + recommendations + top-3 crops |
| `/api/reports` | GET/POST | Saved report history / save a report (optionally linked to a farmer & plot) |
| `/api/reports/plots` | GET | Plots with report counts |
| `/api/reports/improvement` | GET | Latest vs previous report with per-nutrient deltas |
| `/api/reports/{id}` | GET | One report + linked farmer/plot context (404 if unknown) |
| `/api/reports/{id}/cost-estimate` | GET | Fertilizer cost estimate (ETB) recomputed from a saved report |
| `/api/farmers` | GET/POST | Farmer list with plot/test counts / register a farmer |
| `/api/farmers/{id}` | GET | Full profile: farmer + plots (with latest status) + reports (with outcomes) |
| `/api/plots` | POST | Register a plot under a farmer |
| `/api/reports/{id}/outcome` | GET/POST | Harvest outcome follow-up (upsert per report) |
| `/api/iot/readings` | POST | Device ingest — one standardized reading (requires `X-Device-Token`) |
| `/api/iot/readings` | GET | Recent readings for a device, newest first (feeds the dashboard charts) |
| `/api/iot/devices` | GET/POST | Device registry with derived status / register a device (returns token) |
| `/api/iot/devices/{id}` | GET | One device with plot binding + latest reading |
| `/api/iot/devices/{id}/assessment` | GET | Live fertility assessment for a device's latest reading |
| `/api/iot/readings/{id}/report` | POST | Persist a reading into the soil-test history (source stays `iot_simulator`) |
| `/api/iot/simulation` | GET/POST | In-process simulator state / start / stop / switch scenario |
| `/api/iot/alerts` | GET | Advisory alerts (filters: device, active/resolved/all) + `active_count` for the badge |
| `/api/iot/alerts/{id}/ack` | POST | Agent acknowledges an active alert (resolved as `acked`) |
| `/api/iot/demo/run` | POST | One-click demo: ensure device + backfill N readings (resettable) + advisories |
| `/api/iot/readings/export` | GET | CSV download of a device's readings (`Content-Disposition: attachment`) |

`POST /api/predict` example:

```json
{
  "N": 55, "P": 20, "K": 40, "ph": 5.5,
  "crop": "rice",
  "temperature": 25, "humidity": 80, "rainfall": 200
}
```

- **Units**: `input_units` accepts `"kg_ha"` (default, model units) or `"lab"` —
  EthioSIS/ESRI lab-report units (total N %, available P mg/kg, exchangeable K
  cmolc/kg) converted to kg/ha at the API boundary. Responses are always in
  model units and echo the `input_units` they arrived in.
- **Suitability overlay**: when `temperature` + `rainfall` are provided, each
  `crops[]` entry gains an additive `suitability: {class, reasons}` field
  (S1/S2/S3/N from the Ethiopian crop matrix).

### IoT contract (simulated today, ESP32-ready tomorrow)

Every reading — from the in-process simulator, the CLI tool, or a future
physical device — uses the same JSON contract:

```json
{
  "deviceId": "SOIL-001",
  "timestamp": "2026-09-28T10:15:00Z",
  "measurements": {
    "ph": 6.5, "nitrogen": 55, "phosphorus": 25, "potassium": 110,
    "moisture": 45, "temperature": 22
  },
  "units": { "nitrogen": "kg/ha", "phosphorus": "kg/ha", "potassium": "kg/ha",
             "moisture": "%", "temperature": "°C", "ph": "pH" },
  "location": { "latitude": 7.95, "longitude": 39.13 },
  "heartbeat": { "battery": 86.5, "signal": -60 }
}
```

- **Auth**: register the device (`POST /api/iot/devices`) → one-time `token`;
  send it as `X-Device-Token` on every reading. Auth runs **before** the device
  lookup: a missing/invalid token and an unknown device both return the same
  `401` — no device-ID enumeration.
- **Idempotency**: re-sending an identical reading (same device + timestamp) is
  a no-op replay — the original reading id comes back with `duplicate: true`,
  no second row is stored and no duplicate alerts are raised.
- **Units**: N/P/K in **kg/ha** (the model's units). Converting raw sensor
  output into these units is a firmware concern.
- **Quality pipeline**: physical bounds (422), timestamp >5 min in the future
  (422); a reading older than 24h or jumping further than
  a threshold from the previous one is **stored with a warning flag**
  (`STALE_TIMESTAMP`, `SUDDEN_VALUE_CHANGE`) instead of being dropped.
- **Simulated data**: everything today is written with
  `source = "iot_simulator"` and shown with a "Simulated data" badge in the UI.
  Battery and signal are simulated too.

Run one simulator (not both), then open http://localhost:3000/iot:

```bash
# in-process (server-side, driven from the dashboard)
# → press "Start simulator" on /iot

# or the external CLI tool (shares the same generator in iot.py)
python3 tools/iot_simulator.py --device SOIL-001 --token soil001-demo-token \
    --scenario dry --interval 2 --count 3
```

### Advisory alerts, Run Demo & export (Phase 3)

- **Advisory engine** (`alerts.py`): every stored reading is evaluated against
  documented thresholds — moisture (<12 % critical, <20 % low), pH (<4.5 / <5
  acidic, >8.5 / >8 alkaline), N (<20 / <40 kg/ha), temperature (outside
  10–35 °C), battery (<20 %), plus the data-quality flags
  (`STALE_TIMESTAMP`, `SUDDEN_VALUE_CHANGE`). Rules are mutually exclusive per
  metric; at most **one active alert per device+type**; alerts **auto-resolve**
  when the condition clears and can be **acknowledged** (`acked`). Alerting is
  guarded so it can never fail an ingest.
- **UI**: bell badge in the header (site-wide, 30 s poll) + `AlertPanel` on
  `/iot` with severity chips, per-type localized labels (EN/AM) and an
  "Open farmer profile →" deep link (`/agent/{farmer_id}`).
- **Run demo** (`POST /api/iot/demo/run` or the *Run demo* button): creates
  `SOIL-001` if missing (same identity as `tools/seed_demo.py`), backfills
  `count` readings (default 20) at 5-minute steps over the last ~100 minutes —
  all fresh (no stale flag) — through the chosen scenario's random walk.
  Default `reset=true` replaces the device's readings for a deterministic demo.
- **CSV export**: `GET /api/iot/readings/export?device_id=SOIL-001` →
  chronological CSV (ids, timestamps, quality/flags, all values).
- **Optional notification e-mail** (in-app alerts always work):
  set `ALERT_EMAIL_TO` **and** `SMTP_HOST` (plus optional `SMTP_PORT`,
  `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`) → new alerts are queued in an
  outbox table and delivered by a background worker (3 attempts, timeout 10 s).
  With no SMTP settings **no sockets are opened and no e-mail rows are
  written** — the offline demo path.

### Control-plane hardening (Phase 4)

- **Opt-in control token (F3)**: set the `IOT_CONTROL_TOKEN` environment
  variable to require `X-Control-Token` (constant-time compare) on the control
  endpoints — `POST /api/iot/simulation` and `POST /api/iot/demo/run`.
  Unset (the default) keeps the dashboard demo buttons working with no
  configuration; a wrong or missing token returns `401` and the token is never
  logged. `GET /api/iot/simulation` (state) stays open for dashboards.
- **Single writer for the demo device (F4)**: `POST /api/iot/demo/run`
  returns `409` while another demo run is in progress, stops any in-process
  simulator bound to `SOIL-001` first (`simulation_stopped: true` in the
  response), and `POST /api/iot/simulation` with `action: start` returns `409`
  while a demo run is active (stop remains allowed).

### Hardware roadmap (concept docs)

The future device/PCB concepts are documented — not built — in
[`docs/ESP32_FIRMWARE.md`](docs/ESP32_FIRMWARE.md) (contract → firmware
mapping, calibration, HTTP→MQTT migration) and
[`docs/PCB_ARCHITECTURE.md`](docs/PCB_ARCHITECTURE.md) (block diagram,
sensors, power budget, comms, concept BOM).

## Frontend (Next.js)

A Stitch-style web UI lives in `frontend/` (Next.js 16, React 19, TypeScript,
Tailwind v4). Design system: deep forest green `#14532d`, soil brown `#92400e`,
harvest gold `#d97706`, cream surface `#fcf9f4`; Fraunces display serif +
Inter body; 3-step wizard (Analyzer → Fertility Results → Crop Suggestions)
with an animated SVG confidence gauge, per-nutrient cards with status pills and
ideal-range markers, a color-coded action plan, best-match crop card with
temperature/rainfall tiles, climate accordion, and a multilingual switcher
(English, Amharic).

A site-wide header (Analyzer / Agent portal / IoT dashboard) carries the shared
language preference — it persists across pages in `localStorage`. Results include
a **save panel** that links the report to an existing plot of a farmer or creates
a new farmer/plot inline. The **Agent portal** (`/agent`) lists farmers and
(`/agent/{id}`) shows their plots, soil-test history and yield outcomes.
Every report row carries a **Print** button that opens a clean print layout
(`/print/{reportId}`) with the sample table, action plan, top crops and the
fertilizer cost estimate — print or save as PDF straight from the browser
(site header and toolbars are hidden in print).

The **IoT dashboard** (`/iot`) shows device status (online/offline, last seen,
simulated battery/signal), scenario controls for the simulator, the latest
reading, hand-rolled SVG trend charts (N, moisture, pH, temperature), a live
assessment reusing the same fertility model, and a "save reading to history"
action. It polls every 5 seconds (paused while the tab is hidden).

Seed the demo data (Abebe Getachew, Oromia-Arsi, 2.1 ha, two soil tests, a
harvest outcome and the SOIL-001 device with token `soil001-demo-token`) into
the SQLite database:

```bash
python3 tools/seed_demo.py            # idempotent; --force reseeds, --db PATH
```

Requirements: Node.js 18+.

```bash
# terminal 1 — backend API
uvicorn api:app --port 8000

# terminal 2 — frontend
cd frontend
npm install
npm run dev          # http://localhost:3000
```

Set `NEXT_PUBLIC_API_URL` to point at the API if it isn't on port 8000.

Build for production: `npm run build && npm start`.

### Troubleshooting

**"Something went wrong — Cannot reach the analysis API at http://localhost:8000"**

The API is not running, or the browser is blocking cross-origin calls.

1. Start the backend first and keep it running: `uvicorn api:app --port 8000`.
2. Check it is reachable: open http://localhost:8000/api/health in the browser
   (should return `{"status":"ok","models":"loaded","model_version":"random-forest-v2-corrected-labels"}` —
   `status` becomes `"degraded"` if the model files fail to load).
3. If the API runs on a different origin/port, set `NEXT_PUBLIC_API_URL`
   (frontend) and add that origin to `API_ALLOWED_ORIGINS` (backend,
   comma-separated) — the backend refuses browser calls from unlisted origins
   (CORS). Default allowed origins: `http://localhost:3000,http://127.0.0.1:3000`.
4. The UI shows a status pill (green "API online" / red "API offline") and a
   "Retry connection" button on errors — it re-checks automatically every 10s.

## Project Structure

```text
Soil-Analysis/
├── config.py                      # Single source of truth: paths, ideal ranges, features
├── api.py                         # FastAPI REST layer (models + engine + history + agent + IoT)
├── iot.py                         # IoT contract, quality rules, shared simulator generator
├── alerts.py                      # Advisory rules engine + env-gated e-mail outbox (Phase 3)
├── fertilizer_cost.py             # kg/ha → product quantities × indicative prices (Phase 4)
├── db.py                          # SQLite persistence: reports, farmers, plots, outcomes,
│                                  # devices, alerts, fertilizer prices
├── i18n.py                        # en / hi / ta / te / am translations (Streamlit)
├── main.py                        # Streamlit frontend + backend integration
├── data_analysis.py               # Rule-based fertility labeling (regenerates soil_data.csv)
├── frontend/                      # Next.js + Tailwind v4 UI (Stitch design)
│   └── src/
│       ├── app/                   # page.tsx (3-step wizard), agent/ (portal),
│       │                          # agent/[id]/ (profile), iot/ (live dashboard),
│       │                          # print/[reportId]/ (printable report), layout
│       ├── components/            # SiteHeader, Hero, StepNav, NutrientSlider,
│       │                          # PhSlider, FertilityGauge, NutrientCard,
│       │                          # ActionPlan, CropMatchCard, ClimateAccordion,
│       │                          # SavePanel, OutcomeForm, IoTStatusCard,
│       │                          # ReadingGrid, TrendChart, AssessmentPanel,
│       │                          # AlertPanel, PrintableReport
│       ├── lib/                   # api.ts (typed client), lang-context.tsx, useCountUp
│       └── i18n/strings.ts        # en / am translations (frontend)
├── ML/
│   ├── soil_analysis.pkl          # Trained Random Forest (fertility)
│   ├── crop_recommendation.pkl    # Trained Random Forest (crop)
│   ├── crop_bands.json            # Real per-crop nutrient bands (dataset percentiles)
│   ├── engine.py                  # Deterministic analysis + recommendation engine
│   ├── crop_bands.py              # Generates crop_bands.json from the dataset
│   ├── ml_model.py                # Trains the fertility model
│   └── crop_model.py              # Trains the crop model
├── Dataset/
│   ├── soil_data.csv              # Labeled soil dataset (generated)
│   ├── variety_bands.json         # Ethiopian variety target bands (advisory, unverified)
│   ├── seasonal_bands.json        # Meher/Belg seasonal N bands (advisory, unverified)
│   ├── soil_conditioning.json     # SOC/texture band deltas (advisory, unverified)
│   ├── woreda_soil_defaults.json  # Optional Woreda texture/SOC fallbacks (empty skeleton)
│   └── Crop_recommendation.csv    # Source dataset (N, P, K, temperature, humidity, ph, rainfall, crop)
├── variety_bands.py               # Variety lookup + crop/region alias resolution
├── seasonal_bands.py              # Meher/Belg lookup, aliases and N-band application
├── soil_conditioning.py           # SOC/texture lookup, aliases and band deltas
├── tools/
│   ├── seed_demo.py               # Demo farmer + plots + reports + outcome + SOIL-001 device
│   ├── iot_simulator.py           # External IoT CLI: sends contract readings over HTTP
│   └── review_amharic.py          # LLM review helper for Amharic strings
├── docs/
│   ├── ESP32_FIRMWARE.md          # Concept: how future firmware sends the same contract
│   └── PCB_ARCHITECTURE.md        # Concept: block diagram, sensors, power, comms, BOM
├── tests/                         # 566 unit tests (stdlib unittest)
├── requirements.txt
└── README.md
```

## How the data and models are real

- **Per-crop bands** (`ML/crop_bands.json`) are computed from the actual 100 rows per crop
  in `Crop_recommendation.csv` (40th–60th percentile per nutrient). Degenerate columns are
  widened around the median. No invented numbers.
- **Fertility labels** come from a contiguous rule-based scoring index (score ≥5 Good,
  ≥3 Moderate) anchored to agronomic bands; the Random Forest reaches ~97% shuffled 5-fold CV.
- **Recommendations** are calculated: `deficit ÷ nutrient content` (urea 46% N, DAP 46% P₂O₅,
  K₂SO₄ 50% K₂O), lime ~2 t/ha per 0.5 pH unit, sulfur ~1.5 t/ha per 0.5 unit.
- **History** is stored in SQLite (`soil_reports.db`) with real timestamps, and improvement
  deltas are computed from saved reports — never mocked.
- **Crop suggestions** come from the trained crop model's `predict_proba`; climate defaults
  are the dataset medians when not provided.

## Ethiopian standards & crop suitability (EthioSIS)

The default fertility profile is **EthioSIS/ESRI** (`ethiopian_standards.py`),
anchored to published Ethiopian Soil Information System lab thresholds:

| Nutrient | Lab unit (EthioSIS) | "Good" core | Model unit (kg/ha) |
|---|---|---|---|
| Nitrogen | total N % (Kjeldahl) | 0.15–0.25 % | × 400 |
| Phosphorus | available P mg/kg (Olsen/Bray-1) | 30–45 mg/kg | × 2.6 (bulk density 1.3, 0–20 cm; override with `ETHIO_P_FACTOR`) |
| Potassium | exchangeable K cmolc/kg | 0.2–0.4 cmolc/kg | × 391 |
| pH | — | 6.5–7.5 | — |

- `FERTILITY_PROFILE=ethiosis` (default) or `generic` (legacy pre-EthioSIS
  bands + the backup model `ML/soil_analysis_generic.pkl`) selects the
  profile; `ETHIO_N_FACTOR` overrides the N conversion factor (default 400),
  `ETHIO_P_FACTOR` the P conversion factor (default 2.6).
- The fertility model was **retrained** on soil data relabeled with these
  bands (`MODEL_VERSION = random-forest-v4-ethiosis-p2.6`). Good/Moderate/
  Poor labels, JSON keys and all API contracts are unchanged.
- `Dataset/ethiopian_crop_matrix.json` holds S1/S2/N land-suitability bands
  (pH, temperature, rainfall) for **34 crops** — the 22 ML crops plus 12
  Ethiopian staples (barley, enset, faba_bean, garlic, khat, onion, potato,
  sesame, sorghum, teff, tomato, wheat) with Amharic names and regions.
  Rules evaluate only the parameters you provide; the worst parameter wins;
  N-exclusions are strict. The 22-crop ML model is untouched — staples are
  rule-only and never enter the fertility classifier.
- **Lab input**: the Streamlit sidebar and the Next.js analyzer both offer a
  kg/ha ↔ lab-report unit toggle. Lab values are converted internally
  (client-side in the web UI, at the API boundary for external clients) so
  saved reports, fertilizer math and responses always stay in kg/ha.
- Honest limitation: the N × 400 factor is a conventional rule of thumb
  (0–10 cm plow layer, ~1.5 g/cm³ bulk density) — real conversion depends on
  bulk density and organic matter. The matrix bands are compiled
  literature/agronomy guidance, not local field trials.

## Agroecological zones (EIAR)

`Dataset/agroecological_zones.json` defines four Ethiopian agroecological
zones with EIAR research-center nutrient bands (kg/ha, 0–20 cm; P via the
2.6 factor) and machine-readable agronomy overrides:

| Zone id | Name | Areas | Distinctive override |
|---|---|---|---|
| `highland_nitisols` | Highland Nitisols | Jimma, Kaffa, Bench-Sheko, Gedeo | lime-first below pH 5.5; P fixation scaling (÷0.4) below pH 5.0 |
| `highland_vertisols` | Highland Vertisols | central highlands, Tigray lowlands | split N (3 applications); K fertilizer bypassed above 400 kg/ha soil K |
| `rift_valley` | Rift Valley Soils | Meki, Ziway, SNNPR lowlands, Awash | basal blend shifts to NPSZnB above pH 7.5 (Zn/B locking) |
| `arid_semi_arid` | Lowland Arid / Semi-Arid | Somali, Afar, Borena | ECe > 4.0 dS/m pauses fertilizer (gypsum + leaching guidance); ESP > 15% adds a sodicity warning |

**How it plugs in** (all additive — no zone supplied means the exact legacy
generic/crop-band behavior):

- `POST /api/predict` accepts `agroecological_zone` (unknown ids → 400) plus
  optional `ec_e` (dS/m) and `esp` (%). Zone bands **replace** both the
  generic and per-crop Kaggle bands for N/P/K/pH; the response gains an
  `agroecology` block {zone, name, bands, constraint, source[, salinity]}.
- Saved reports store the zone (and ECe/ESP), so history and
  `/api/reports/{id}/cost-estimate` recompute with the same zone rules —
  a saline arid report costs **0 ETB/ha** (fertilizer paused).
- The Next.js analyzer and the Streamlit app both have a zone selector;
  reports saved from the web UI keep the zone.
- `SOIL_MODEL_PROFILE=agroecology` (or `ethio`) serves the zone-aware
  fertility artifact `ML/soil_analysis_ethio.pkl` (one-hot zone feature,
  `random-forest-v5-agroecology-zones`); the default profile keeps the
  legacy `ML/soil_analysis.pkl` (`random-forest-v4-ethiosis-p2.6`).
- Zone training data: `tools/generate_zone_dataset.py` samples N/P/K/pH per
  zone (12,000 reproducible rows, sha256 in the `.meta.json` sidecar) and
  labels each row with that row's zone bands.

## Variety-specific bands (Enhancement C)

`Dataset/variety_bands.json` holds 10 advisory crop × production-area ×
variety records for maize, wheat, teff, sorghum, coffee and faba bean. They
are **target ranges compiled from EIAR/CIMMYT/JARC/regional notes**, not
measured soil observations — they are deliberately *not* used as supervised
training rows and are marked `review_status: "unverified"` until checked
against the cited publications.

**Activation is explicit and additive** (omitting `variety` keeps the exact
legacy behavior):

- `POST /api/predict` accepts optional `variety` plus `region`. `crop` is
  required with a variety; unknown variety/region/crop combinations → 400.
- Precedence is **variety > zone > crop > generic**. Supplying a zone as well
  selects its machine-readable agronomy overrides (lime-first, split N,
  salinity pause, etc.) while the variety bands drive N/P/K/pH.
- Non-ML crops (wheat, teff, sorghum, faba bean) are accepted when a valid
  variety (or, in Enhancement D, a valid season) is supplied; without one
  they retain the legacy 400 rejection.
- The response gains a `variety` block with the resolved id, labeled bands,
  source and review status. The bands also populate each recommendation's
  `ideal_range`, so saved reports and cost estimates match.
- Saved reports store `region` and `variety`; `/api/reports/{id}` returns them
  and `/api/reports/{id}/cost-estimate` recomputes with the same bands.
- The Next.js analyzer has a named-variety selector and displays the resolved
  bands/source. `GET /api/varieties` exposes the lookup for other clients.
- ML artifacts (`ML/soil_analysis*.pkl`, `ML/crop_recommendation.pkl`) and
  the 22-crop `/api/crops` contract are unchanged.

## Seasonal bands (Enhancement D)

`Dataset/seasonal_bands.json` holds **6 crops × 2 seasons** (meher/belg) of
advisory nitrogen targets for maize, wheat, teff, sorghum, potato and faba
bean. P/K/pH are season-independent; only N changes. The data is
`review_status: "unverified"` and is deliberately **not** used as supervised
training rows. No ML artifact or default prediction path was retrained.

**Activation is explicit and additive** (omitting `season` keeps the exact
legacy behavior):

- `POST /api/predict` accepts optional `season` (`meher`, `belg`, or aliases
  such as `kiremt`/`short rains`). `crop` is required; unknown or unsupported
  crop-season combinations → 400.
- With no variety/zone, the seasonal crop baseline supplies N plus Ethiopian
  P/K/pH. With a variety/zone, P/K/pH are preserved, Meher keeps the base N
  band, and Belg scales it by the crop's `n_factor`
  (Belg N midpoint / Meher N midpoint).
- Precedence is **season-modified variety > season-modified zone > seasonal
  crop > generic**. Zone agronomy overrides (lime-first, split N, salinity
  pause) still apply.
- Non-ML crops (wheat, teff, sorghum, faba bean) are accepted with a valid
  season; without a season or variety they retain the legacy 400 rejection.
- The response gains a `season` block with the resolved id, bands, scaling
  source, notes and review status. Recommendations' `ideal_range` and saved
  report cost estimates use the same seasonal bands.
- Saved reports store `season`; `/api/reports/{id}` returns it and
  `/api/reports/{id}/cost-estimate` recomputes with the same N target.
- The Next.js analyzer shows a season selector for supported crops and
  displays the resolved bands. `GET /api/seasons` exposes the reference data.

## SOC/texture conditioning (Enhancement E)

`Dataset/soil_conditioning.json` holds **unverified advisory deltas** for soil
organic carbon (SOC → N) and texture (K, P; P also checks pH). They are
applied to the already-resolved variety/zone/crop/generic bands — never as
absolute replacement bands — because the supplied matrix conflicts with the
Ethiopian zone bands. No ML artifact was retrained.

**Activation is explicit and additive** (omitting all new inputs keeps the
exact legacy behavior):

- `POST /api/predict` accepts optional `texture_class` (`sandy`/`loam`/`clay`
  plus aliases), `soc_pct` (numeric %), `soc_class`
  (`very_low`/`low`/`medium`/`high`) and `woreda`. A numeric `soc_pct` wins
  over `soc_class`; unknown classes → 400, unknown woredas fall through.
- `SOIL_CONDITIONING_ENABLED=true` activates zone SOC defaults even without
  explicit inputs. `SOIL_CONDITIONING_USE_SOIL_TYPE=true` additionally lets
  `soil_type` supply a texture default. Neither flag is on by default.
- Conditioning is applied **after** the base bands and **before** the
  seasonal N modifier, then existing zone agronomy overrides still run.
- The response gains an additive `soil_conditioning` block with the resolved
  texture/SOC, source levels, and per-nutrient `before → after` deltas plus
  `review_status: "unverified"`. Recommendations for adjusted nutrients get
  a short provenance note.
- Saved reports store `texture_class`, `soc_pct`, `soc_class` and `woreda`;
  `/api/reports/{id}` returns them and cost estimates recompute with the same
  bands. `GET /api/soil-conditioning` exposes the reference data.
- The Next.js analyzer has optional texture/SOC/woreda fields and displays
  the applied deltas.

## Ethiopian lime requirement by soil type (LRF)

`Dataset/lime_requirement.json` (EIAR/Holetta, DZARC, Haramaya, EthioSIS,
MARC) holds **Lime Requirement Factors** — the tons of 90%-ECCE agricultural
lime needed to lift pH by 1.0 unit across 1 hectare at 20 cm plow depth:

| Soil type | LRF (t/ha per pH unit) | Buffering |
|---|---:|---|
| `nitisol` | 4.2 | very high (Al/Fe oxides) |
| `vertisol` | 5.5 | extreme (smectite clay) |
| `cambisol_luvisol` | 2.8 | moderate |
| `andosol` | 3.5 | high (allophane/organic) |
| `arenosol` | 1.2 | very low (sandy) |

**Safety rules encoded:** the single application never exceeds **5.0 t/ha** —
heavier programs are split across consecutive seasons (the total is never
capped); dolomitic lime multiplies the total by **0.84** (higher neutralizing
value per tonne).

**How it plugs in** (all additive — without `soil_type` the legacy generic
formula `min(pH deficit/0.5 × 2.0, 10.0) t/ha` applies unchanged):

- `POST /api/predict` accepts `soil_type` (unknown → 400) and `lime_type`
  (`calcitic`|`dolomitic`). When the sample is pH-deficient the response
  gains a `lime_plan` block {soil_type, lrf, total_t_ha, max_single_t_ha,
  schedule[{season, t_ha}], …} and the pH recommendation text quotes the
  same LRF number plus the season schedule.
- `GET /api/reports/{id}/cost-estimate?soil_type=vertisol&lime_type=dolomitic`
  prices the **total program** (all seasons) at the LRF rate.
- One function (`ethiopian_standards.lime_rate_t`) feeds both the report
  text and the cost line, so they can never disagree.
- The Next.js analyzer has an optional "Soil type" selector + lime-product
  toggle; picking none keeps the legacy rate.

**v6 fertility model (opt-in):** `python3 tools/generate_zone_dataset.py
--soil-type --out Dataset/soil_data_ethio_v6.csv` adds a within-zone
`soil_type` column; training it with `SOIL_MODEL_OUT=ML/soil_analysis_ethio_v6.pkl
SOIL_MODEL_TAG=random-forest-v6-soiltype-lrf` produces the v6 artifact
(one-hot zone + soil_type features). Serve it with
`SOIL_MODEL_PROFILE=agroecology_v6`; the default profile and the v5
artifact are untouched.

## Limitations (please read)

- Advice amounts are **estimates from standard agronomy rules of thumb** — always
  verify with a soil-testing lab and local extension service before applying fertilizer.
- The fertility label is derived from a rule-based index on the public
  `Crop_recommendation.csv` dataset, not from lab-measured fertility ground truth.
- The zone-synthetic v5/v6 datasets are **rule distillation, not field data**:
  their labels are computed from the same bands they were sampled around, so
  stratified CV measures rule reproduction. Holding out a whole zone drops
  accuracy to ~0.44–0.56. See [`docs/DATA_CARD.md`](docs/DATA_CARD.md).
- **Label semantics**: the model returns a zone-level Good/Moderate/Poor triage.
  The per-nutrient advice and amounts are produced by the deterministic band
  engine (`variety > season > zone > crop > generic`), which stays
  authoritative; a "Good" triage can still show a crop-specific P deficit.
- `/api/crops` bands come from `Crop_recommendation.csv` — a
  **non-Ethiopian baseline** (`bands_source` in each entry). The Ethiopian
  matrix (`/api/crops/ethiopian`) and the variety/season/zone layers are
  separate, rule-based additions.
- Crop bands describe "what the dataset shows for that crop" — use them as guidance,
  not as a substitute for local crop research recommendations.
- Predictions outside the training ranges (N 0–140, P 5–145, K 5–205, pH 3.5–9.94)
  are extrapolations and are flagged in the UI.

## Regenerate data / retrain models

```bash
python3 data_analysis.py      # regenerates Dataset/soil_data.csv
python3 ML/crop_bands.py      # regenerates ML/crop_bands.json
python3 ML/ml_model.py        # retrains + saves ML/soil_analysis.pkl
python3 ML/crop_model.py      # retrains + saves ML/crop_recommendation.pkl

# Zone-aware fertility artifact (separate model, trained on zone-synthetic data)
python3 tools/generate_zone_dataset.py            # Dataset/soil_data_ethio.csv
SOIL_TRAIN_DATA=Dataset/soil_data_ethio.csv \
SOIL_MODEL_OUT=ML/soil_analysis_ethio.pkl \
SOIL_MODEL_TAG=random-forest-v5-agroecology-zones \
python3 ML/ml_model.py

# v6: adds the within-zone soil_type column for the Ethiopian LRF schema
python3 tools/generate_zone_dataset.py --soil-type \
    --out Dataset/soil_data_ethio_v6.csv
SOIL_TRAIN_DATA=Dataset/soil_data_ethio_v6.csv \
SOIL_MODEL_OUT=ML/soil_analysis_ethio_v6.pkl \
SOIL_MODEL_TAG=random-forest-v6-soiltype-lrf \
python3 ML/ml_model.py

python3 -m unittest discover -s tests   # run the test suite
```

Training logs the data path, dataset sha256, feature columns and
hyperparameters to MLflow, so every artifact is reproducible from its run.

### Real Ethiopian data → v7 (gated)

Capture is opt-in at two levels: `TRAINING_DATA_CAPTURE=true` on the server
**and** the consent box in the save panel (the checkbox only appears when
`GET /api/health` reports `training_capture: true`). Captured samples are
unlabeled and unusable until a lab sheet is imported:

```bash
# 1. attach verified labels (dry run first; --require-coverage gates a release)
python3 tools/import_lab_results.py --csv lab_results.csv --apply
python3 tools/import_lab_results.py --csv lab_results.csv --apply --require-coverage

# 2. export real rows + combine with the v6 baseline (real rows weighted 1.0)
python3 tools/export_training_data.py \
    --combine-with Dataset/soil_data_ethio_v6.csv \
    --combined-out Dataset/soil_data_ethio_v7.csv \
    --real-weight 1.0 --synthetic-weight 0.3 --min-per-class 30 --force

# 3. honest gate: real-only holdout, grouped by site, vs the band rule
python3 tools/evaluate_ethio_model.py --csv Dataset/soil_data_ethio_v7.csv \
    --min-macro-f1 0.65 --require-beats-baseline --leave-one-zone-out

# 4. only if the gate passes
SOIL_TRAIN_DATA=Dataset/soil_data_ethio_v7.csv \
SOIL_GROUP_COL=site_id SOIL_SAMPLE_WEIGHT_COL=sample_weight \
SOIL_MODEL_OUT=ML/soil_analysis_ethio_v7.pkl \
SOIL_MODEL_TAG=random-forest-v7-real-ethio \
python3 ML/ml_model.py
```

`SOIL_MODEL_PROFILE=agroecology_v7` serves that artifact; the default profile
is unchanged. Until real verified rows exist the evaluator exits `2` and no v7
artifact should be built. Full provenance and gates:
[`docs/DATA_CARD.md`](docs/DATA_CARD.md) and
[`docs/ETHIOPIAN_TRAINING_PLAN.md`](docs/ETHIOPIAN_TRAINING_PLAN.md).

## MLflow Integration

Tracks precision, recall, F1-score, and 5-fold CV accuracy for the Random Forest
models, plus model versioning for reproducibility.

## License

MIT License

## Acknowledgements

- Streamlit for the interactive UI
- LangChain + Groq for AI integration
- MLflow for experiment tracking
