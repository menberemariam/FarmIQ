"""REST API for the Soil Fertility Analyzer.

Serves the real trained models and the deterministic engine. No mocks:
every response is computed from the actual soil/crop models, the per-crop
bands derived from the dataset, and the SQLite report history.

Run:  uvicorn api:app --host 0.0.0.0 --port 8000
Docs:  http://localhost:8000/docs
"""

import json
import os
from functools import lru_cache

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import config
import db
from i18n import normalize_lang
from ML.engine import analyze_soil, available_crops, get_bands, recommend_soil


def _allowed_origins():
    """Frontend origins allowed to call this API (CORS).

    Configure with API_ALLOWED_ORIGINS (comma-separated URLs). Defaults to the
    local Next.js dev server. The browser blocks cross-origin reads without
    this header, so any frontend origin must be listed here.
    """
    raw = os.getenv("API_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:1212,http://127.0.0.1:1212")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title="Soil Fertility Analyzer API",
    description="Real soil fertility prediction, crop-specific recommendations and history.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PredictRequest(BaseModel):
    N: float = Field(ge=0, description="Nitrogen in kg/ha")
    P: float = Field(ge=0, description="Phosphorus in kg/ha")
    K: float = Field(ge=0, description="Potassium in kg/ha")
    ph: float = Field(ge=0, le=14, description="Soil pH")
    crop: str | None = Field(None, description="Target crop (see /api/crops)")
    temperature: float | None = Field(None, description="Air temperature °C")
    humidity: float | None = Field(None, description="Relative humidity %")
    rainfall: float | None = Field(None, description="Rainfall mm")
    lang: str = Field("en", description="Response language: en or am")


class ReportIn(BaseModel):
    plot: str = Field(min_length=1, description="Plot / field name")
    N: float
    P: float
    K: float
    ph: float
    fertility_status: str
    recommendations: list
    crop: str | None = None
    temperature: float | None = None
    humidity: float | None = None
    rainfall: float | None = None
    crops: list | None = None
    lang: str = "en"


SCIENTIFIC_NAMES = {
    "apple": "Malus domestica",
    "banana": "Musa acuminata",
    "blackgram": "Vigna mungo",
    "chickpea": "Cicer arietinum",
    "coconut": "Cocos nucifera",
    "coffee": "Coffea arabica",
    "cotton": "Gossypium hirsutum",
    "grapes": "Vitis vinifera",
    "jute": "Corchorus olitorius",
    "kidneybeans": "Phaseolus vulgaris",
    "lentil": "Lens culinaris",
    "maize": "Zea mays",
    "mango": "Mangifera indica",
    "mothbeans": "Vigna aconitifolia",
    "mungbean": "Vigna radiata",
    "muskmelon": "Cucumis melo",
    "orange": "Citrus sinensis",
    "papaya": "Carica papaya",
    "pigeonpeas": "Cajanus cajan",
    "pomegranate": "Punica granatum",
    "rice": "Oryza sativa",
    "watermelon": "Citrullus lanatus",
}


@lru_cache(maxsize=1)
def _models():
    return joblib.load(config.SOIL_MODEL_PKL), joblib.load(config.CROP_MODEL_PKL)


@lru_cache(maxsize=1)
def _climate_defaults():
    df = pd.read_csv(config.CROP_DATA_CSV)
    return {
        "temperature": float(df["temperature"].median()),
        "humidity": float(df["humidity"].median()),
        "rainfall": float(df["rainfall"].median()),
    }


@lru_cache(maxsize=1)
def _crop_climate():
    """Per-crop climate medians from the real dataset (drives the match-card tiles)."""
    df = pd.read_csv(config.CROP_DATA_CSV)
    return {
        crop: {
            "temperature": round(float(sub["temperature"].median()), 1),
            "rainfall": round(float(sub["rainfall"].median()), 1),
        }
        for crop, sub in df.groupby("label")
    }


def _ideal_range_string(item):
    lo, hi = item["band"]
    return f"{lo:.0f}-{hi:.0f} kg/ha" if item["col"] != "ph" else f"{lo:.1f}-{hi:.1f}"


def _top_crops(crop_rf, row):
    probs = crop_rf.predict_proba(row)[0]
    order = probs.argsort()[::-1][:3]
    climate = _crop_climate()
    out = []
    for i in order:
        name = crop_rf.classes_[i]
        out.append(
            {
                "crop": name,
                "scientific_name": SCIENTIFIC_NAMES.get(name),
                "confidence": round(float(probs[i]) * 100, 1),
                "temperature": climate[name]["temperature"],
                "rainfall": climate[name]["rainfall"],
            }
        )
    return out


@app.get("/api/health")
def health():
    return {"status": "ok", "models": "loaded"}


@app.get("/api/crops")
def crops():
    """All crops with their real nutrient bands (dataset percentiles)."""
    climate = _crop_climate()
    return {
        "crops": [
            {
                "name": c,
                "scientific_name": SCIENTIFIC_NAMES.get(c),
                "bands": get_bands(c),
                "temperature": climate[c]["temperature"],
                "rainfall": climate[c]["rainfall"],
            }
            for c in available_crops()
        ]
    }


@app.post("/api/predict")
def predict(req: PredictRequest):
    """Predict fertility, per-nutrient recommendations and top-3 crops.

    Returns the same shape the UI renders: fertility_status, confidence,
    recommendations[{element, status, deficit, value, ideal_range,
    solution, source}], crops[{crop, scientific_name, confidence,
    temperature, rainfall}].
    """
    if req.crop and req.crop.lower() not in available_crops():
        raise HTTPException(400, f"Unknown crop '{req.crop}'. See /api/crops")

    raw_lang = (req.lang or "").strip().lower()
    if raw_lang not in ("en", "english", "am", "amh", "am-et", "amharic", "አማርኛ"):
        raise HTTPException(422, "lang must be one of: en, am")
    lang = normalize_lang(req.lang)

    soil_rf, crop_rf = _models()

    soil_row = pd.DataFrame([{"N": req.N, "P": req.P, "K": req.K, "ph": req.ph}])
    fertility_status = soil_rf.predict(soil_row)[0]
    proba = soil_rf.predict_proba(soil_row)[0]
    predicted_idx = list(soil_rf.classes_).index(fertility_status)
    confidence = round(float(proba[predicted_idx]) * 100, 1)

    analysis = analyze_soil(req.N, req.P, req.K, req.ph, crop=req.crop)
    recommendations = [
        {
            "element": r["element"],
            "status": r["status"],
            "deficit": r["deficit"],
            "value": analysis[i]["value"],
            "ideal_range": _ideal_range_string(analysis[i]),
            "solution": r["solution"],
            "source": r["source"],
            "lang": lang,
        }
        for i, r in enumerate(recommend_soil(analysis, lang=lang))
    ]

    climate = _climate_defaults()
    row = pd.DataFrame(
        [{
            "N": req.N, "P": req.P, "K": req.K,
            "temperature": req.temperature if req.temperature is not None else climate["temperature"],
            "humidity": req.humidity if req.humidity is not None else climate["humidity"],
            "ph": req.ph,
            "rainfall": req.rainfall if req.rainfall is not None else climate["rainfall"],
        }]
    )[config.CROP_FEATURES]
    crop_recs = _top_crops(crop_rf, row)

    return {
        "fertility_status": fertility_status,
        "confidence": confidence,
        "lang": lang,
        "recommendations": recommendations,
        "crops": crop_recs,
    }


@app.get("/api/reports")
def reports(plot: str | None = Query(None), limit: int = Query(20, ge=1, le=100)):
    """Saved report history, newest first. Optionally filter by plot."""
    return {"reports": db.list_reports(plot=plot, limit=limit)}


@app.get("/api/reports/plots")
def report_plots():
    return {"plots": db.list_plots()}


@app.get("/api/reports/improvement")
def improvement(plot: str):
    """Latest vs previous report for a plot, with real per-nutrient deltas."""
    result = db.latest_two(plot)
    if result is None:
        raise HTTPException(404, f"Not enough reports for plot '{plot}'")
    return result


@app.post("/api/reports")
def save_report(report: ReportIn):
    report_id = db.save_report(
        report.plot, report.N, report.P, report.K, report.ph,
        report.fertility_status, report.recommendations,
        crop=report.crop, temperature=report.temperature,
        humidity=report.humidity, rainfall=report.rainfall, crops=report.crops,
        lang=normalize_lang(report.lang),
    )
    return {"id": report_id, "saved": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
