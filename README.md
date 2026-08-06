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
- **Optional LLM enrichment** (Groq) when `GROQ_API_KEY` is set: the AI's answer is
  validated against the computed deficit per element and automatically replaced by the
  deterministic calculation if it contradicts it.
- **Crop recommendation**: top-3 suitable crops for your soil + climate (22 crops,
  ~99% CV accuracy).
- **Report history & improvement tracking**: save every prediction to SQLite per plot/field;
  re-test after a season and see real before/after deltas (N +20, P +8, pH +0.9...) and
  fertility class changes, plus a nutrient trend chart.
- **🌐 Multilingual UI** — English, हिन्दी, தமிழ், తెలుగు (language switcher in the sidebar).
- **REST API** (`/api/predict`, `/api/crops`, `/api/reports`) — ready for the React/Next.js UI.
- Warnings when inputs fall outside the training data range (no silent extrapolation).
- Cross-platform (`pathlib` paths), 49 unit tests.

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
| `/api/predict` | POST | Fertility status + confidence + recommendations + top-3 crops |
| `/api/reports` | GET/POST | Saved report history / save a report |
| `/api/reports/plots` | GET | Plots with report counts |
| `/api/reports/improvement` | GET | Latest vs previous report with per-nutrient deltas |

`POST /api/predict` example:

```json
{
  "N": 55, "P": 20, "K": 40, "ph": 5.5,
  "crop": "rice",
  "temperature": 25, "humidity": 80, "rainfall": 200
}
```

## Frontend (Next.js)

A Stitch-style web UI lives in `frontend/` (Next.js 16, React 19, TypeScript,
Tailwind v4). Design system: deep forest green `#14532d`, soil brown `#92400e`,
harvest gold `#d97706`, cream surface `#fcf9f4`; Fraunces display serif +
Inter body; 3-step wizard (Analyzer → Fertility Results → Crop Suggestions)
with an animated SVG confidence gauge, per-nutrient cards with status pills and
ideal-range markers, a color-coded action plan, best-match crop card with
temperature/rainfall tiles, climate accordion, and a multilingual switcher
(English, हिन्दी, தமிழ், తెలుగు).

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
   (should return `{"status":"ok","models":"loaded"}`).
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
├── api.py                         # FastAPI REST layer (real models + engine + history)
├── db.py                          # SQLite persistence for soil reports
├── i18n.py                        # en / hi / ta / te translations (Streamlit)
├── main.py                        # Streamlit frontend + backend integration
├── data_analysis.py               # Rule-based fertility labeling (regenerates soil_data.csv)
├── frontend/                      # Next.js + Tailwind v4 UI (Stitch design)
│   └── src/
│       ├── app/                   # page.tsx (3-step wizard), layout, globals.css
│       ├── components/            # Hero, StepNav, NutrientSlider, PhSlider,
│       │                          # FertilityGauge, NutrientCard, ActionPlan,
│       │                          # CropMatchCard, ClimateAccordion
│       ├── lib/                   # api.ts (typed client), useCountUp
│       └── i18n/strings.ts        # en / hi / ta / te translations (frontend)
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
│   └── Crop_recommendation.csv    # Source dataset (N, P, K, temperature, humidity, ph, rainfall, crop)
├── tests/                         # 49 unit tests (stdlib unittest)
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

## Limitations (please read)

- Advice amounts are **estimates from standard agronomy rules of thumb** — always
  verify with a soil-testing lab and local extension service before applying fertilizer.
- The fertility label is derived from a rule-based index on the public
  `Crop_recommendation.csv` dataset, not from lab-measured fertility ground truth.
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
python3 -m unittest discover -s tests   # run the test suite
```

## MLflow Integration

Tracks precision, recall, F1-score, and 5-fold CV accuracy for the Random Forest
models, plus model versioning for reproducibility.

## License

MIT License

## Acknowledgements

- Streamlit for the interactive UI
- LangChain + Groq for AI integration
- MLflow for experiment tracking
