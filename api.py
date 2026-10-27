"""REST API for the Soil Fertility Analyzer.

Serves the real trained models and the deterministic engine. No mocks:
every response is computed from the actual soil/crop models, the per-crop
bands derived from the dataset, and the SQLite report history.

Run:  uvicorn api:app --host 0.0.0.0 --port 8000
Docs:  http://localhost:8000/docs
"""

import asyncio
import csv
import io
import json
import os
import secrets
from functools import lru_cache
from typing import Literal

import joblib
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import config
import db
import ethiopian_standards as ethio
import fertilizer_catalog
import fertilizer_cost
import iot
import seasonal_bands
import soil_conditioning
import variety_bands
from i18n import normalize_lang
from ML.engine import (
    analyze_soil,
    application_schedule,
    available_crops,
    build_soil_row,
    get_bands,
    merge_recommendations,
    micronutrient_check,
    product_for_rec,
    product_selection_block,
    recommend_soil,
    training_range_warnings,
)
from ML.llm import (
    contract_counters,
    get_llm_recommendations,
    stream_llm_recommendations,
)

load_dotenv()

# Matches the MLflow tag set at training time (ML/ml_model.py::default_tag).
MODEL_VERSION = (
    "random-forest-v7-real-ethio"
    if config.SOIL_MODEL_PROFILE == "agroecology_v7"
    else "random-forest-v6-soiltype-lrf"
    if config.SOIL_MODEL_PROFILE == "agroecology_v6"
    else "random-forest-v5-agroecology-zones"
    if config.SOIL_MODEL_PROFILE in ("agroecology", "ethio")
    else "random-forest-v4-ethiosis-p2.6"
)

# Provenance of the per-crop nutrient bands served by /api/crops. The source
# dataset is not Ethiopian; the zone/variety/season layers are separate.
CROP_BANDS_SOURCE = "Crop_recommendation.csv (non-Ethiopian baseline)"


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
    N: float = Field(
        ge=0,
        description="Nitrogen — kg/ha when input_units=kg_ha (default), "
        "total N % (Kjeldahl) when input_units=lab",
    )
    P: float = Field(
        ge=0,
        description="Phosphorus — kg/ha when input_units=kg_ha (default), "
        "available P mg/kg (Olsen/Mehlich-III) when input_units=lab",
    )
    K: float = Field(
        ge=0,
        description="Potassium — kg/ha when input_units=kg_ha (default), "
        "exchangeable K cmolc/kg when input_units=lab",
    )
    ph: float = Field(ge=0, le=14, description="Soil pH")
    crop: str | None = Field(None, description="Target crop (see /api/crops)")
    temperature: float | None = Field(None, description="Air temperature °C")
    humidity: float | None = Field(None, description="Relative humidity %")
    rainfall: float | None = Field(None, description="Rainfall mm")
    lang: str = Field("en", description="Response language: en, am or om")
    input_units: Literal["kg_ha", "lab"] = Field(
        "kg_ha",
        description="Unit system for N/P/K: 'kg_ha' (model units, default) or "
        "'lab' (EthioSIS/ESRI lab report units: % N, mg/kg P, cmolc/kg K — "
        "converted to kg/ha internally)",
    )
    use_llm: bool = Field(
        False,
        description="Ask the LLM for enriched solutions (validated against the "
        "computed analysis; falls back to rule-based on any failure)",
    )
    sulfur_ppm: float | None = Field(
        None, ge=0, le=200,
        description="Soil sulfur, ppm (EthioSIS S soil test). Additive: when "
        "given, the response carries a micronutrients.sulfur block and, with "
        "FERT_PRODUCT_CATALOG=on, NPS/NPSB is selected per the S/B decision "
        "rules (S < 20 ppm critical)",
    )
    boron_ppm: float | None = Field(
        None, ge=0, le=50,
        description="Soil boron, ppm. Additive: response micronutrients.boron; "
        "with catalog mode on, B < 0.5 ppm selects NPSB (B > 2.0 ppm never does)",
    )
    region: str | None = Field(
        None, max_length=100,
        description="Region / production-area name (e.g. 'Gojjam', 'Oromia', "
        "'Arsi/Bale'). Additive: with catalog mode on, blend-target regions "
        "select the region K blend; with a variety, it selects the variety "
        "production-area bands",
    )
    variety: str | None = Field(
        None, max_length=120,
        description="Named Ethiopian variety / variety group for the crop, "
        "e.g. 'BH-660', 'Kuncho', 'Danda'a', 'Washed'. Additive: when supplied "
        "the crop x production-area x variety target bands "
        "(Dataset/variety_bands.json) replace the crop/zone bands for N, P, K "
        "and pH; the response gains a 'variety' block. Requires crop. Without "
        "it the legacy crop/zone/generic path is unchanged",
    )
    season: str | None = Field(
        None, max_length=30,
        description="Ethiopian growing season: 'meher' (main/long rains) or "
        "'belg' (short rains). Additive N-only modifier "
        "(Dataset/seasonal_bands.json): with a crop and no variety/zone, the "
        "seasonal Ethiopian crop baseline supplies N/P/K/pH; with a variety "
        "or zone, P/K/pH are preserved, Meher keeps the base N band and Belg "
        "scales it by the crop's seasonal factor. Requires crop; unknown or "
        "unsupported crop-season combinations -> 400. Without season the "
        "response shape and bands are unchanged",
    )
    agroecological_zone: str | None = Field(
        None, max_length=50,
        description="Ethiopian agroecological zone id: highland_nitisols, "
        "highland_vertisols, rift_valley or arid_semi_arid. Additive: the "
        "zone's EIAR bands replace the generic and crop bands for N, P, K "
        "and pH analysis, and the response gains an 'agroecology' block",
    )
    ec_e: float | None = Field(
        None, ge=0, le=100,
        description="Electrical conductivity of the saturation extract, "
        "dS/m (salinity). Additive: with an arid zone, ECe above the zone "
        "threshold pauses fertilizer recommendations in favour of gypsum "
        "+ leaching guidance",
    )
    esp: float | None = Field(
        None, ge=0, le=100,
        description="Exchangeable sodium percentage (sodicity). Additive: "
        "with an arid zone, ESP above the threshold appends a sodicity "
        "warning to the salinity guidance",
    )
    soil_type: str | None = Field(
        None, max_length=40,
        description="Ethiopian soil type for the lime requirement (LRF): "
        "nitisol, vertisol, cambisol_luvisol, andosol or arenosol (display "
        "names accepted). Additive: with a pH-deficient sample the lime "
        "tonnage switches from the generic formula to the soil-type LRF "
        "rate and the response gains a 'lime_plan' schedule block "
        "(5.0 t/ha single-application ceiling, seasonal split). Omitted "
        "-> legacy lime formula, no lime_plan key",
    )
    lime_type: Literal["calcitic", "dolomitic"] = Field(
        "calcitic",
        description="Agricultural lime product. Additive: dolomitic applies "
        "the Ethiopian x0.84 neutralizing-value adjustment; only meaningful "
        "together with soil_type",
    )
    texture_class: str | None = Field(
        None, max_length=30,
        description="Optional Ethiopian soil texture class for Enhancement E "
        "band conditioning: sandy, loam or clay (aliases accepted). Additive: "
        "adjusts K and P bands when supplied; without it (and without SOC/"
        "woreda inputs) the legacy bands are unchanged",
    )
    soc_pct: float | None = Field(
        None, ge=0, le=20,
        description="Soil organic carbon, percent. Optional Enhancement E "
        "input: <1% raises the N band, >3% lowers it. A numeric value wins "
        "over soc_class",
    )
    soc_class: str | None = Field(
        None, max_length=20,
        description="Qualitative soil organic carbon class: very_low, low, "
        "medium or high. Optional Enhancement E input used when soc_pct is "
        "not supplied",
    )
    woreda: str | None = Field(
        None, max_length=100,
        description="Optional Woreda name for the Woreda-level texture/SOC "
        "fallback (Dataset/woreda_soil_defaults.json). Unknown woredas fall "
        "through to the soil-type/zone defaults without changing legacy "
        "behavior",
    )
    include_application_guide: bool = Field(
        False,
        description="Add an 'application' growth-stage schedule to each "
        "recommendation (splits + restrictions, localized). Additive key — "
        "omitted unless requested",
    )


class SuitabilityRequest(BaseModel):
    ph: float | None = Field(None, ge=0, le=14, description="Soil pH")
    temperature: float | None = Field(None, description="Air temperature °C")
    rainfall: float | None = Field(None, description="Rainfall mm")
    crops: list[str] | None = Field(
        None,
        description="Matrix crops to evaluate (default: the 12 Ethiopian "
        "staples that are not part of the 22-crop ML model)",
    )


class ReportIn(BaseModel):
    plot: str = Field(min_length=1, max_length=100, description="Plot / field name")
    N: float = Field(ge=0, le=1000)
    P: float = Field(ge=0, le=1000)
    K: float = Field(ge=0, le=1000)
    ph: float = Field(ge=0, le=14)
    fertility_status: Literal["Good", "Moderate", "Poor"]
    recommendations: list = Field(max_length=50)
    crop: str | None = None
    temperature: float | None = None
    humidity: float | None = None
    rainfall: float | None = None
    crops: list | None = None
    lang: str = "en"
    farmer_id: int | None = Field(None, ge=1, description="Owning farmer")
    plot_id: int | None = Field(None, ge=1, description="Registered plot")
    source: Literal["manual", "lab", "iot_simulator", "iot_device"] = "manual"
    consent_for_training: bool = Field(
        False,
        description="Explicit consent to store this report as an unlabeled "
        "sample for future Ethiopian model retraining. Off by default; "
        "capture also requires TRAINING_DATA_CAPTURE to be enabled. The "
        "sample is never used until a lab/expert label is verified",
    )
    soil_type: str | None = Field(
        None, max_length=40,
        description="Ethiopian soil type (nitisol/vertisol/...). Additive "
        "training context; when provided it is validated and stored on the "
        "training sample, not on the report row",
    )
    sulfur_ppm: float | None = Field(None, ge=0, le=200)
    boron_ppm: float | None = Field(None, ge=0, le=50)
    region: str | None = Field(None, max_length=100)
    variety: str | None = Field(None, max_length=120)
    season: str | None = Field(None, max_length=30)
    agroecological_zone: str | None = Field(None, max_length=50)
    ec_e: float | None = Field(None, ge=0, le=100)
    esp: float | None = Field(None, ge=0, le=100)
    texture_class: str | None = Field(None, max_length=30)
    soc_pct: float | None = Field(None, ge=0, le=20)
    soc_class: str | None = Field(None, max_length=20)
    woreda: str | None = Field(None, max_length=100)


class FarmerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=30)
    region: str | None = Field(None, max_length=100)
    lang: str = "en"


class PlotIn(BaseModel):
    farmer_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    farm_size_ha: float | None = Field(None, ge=0, le=100000)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    previous_crop: str | None = Field(None, max_length=50)


class OutcomeIn(BaseModel):
    implementation_notes: str | None = Field(None, max_length=2000)
    implemented_at: str | None = Field(None, max_length=30)
    expected_yield: float | None = Field(None, ge=0)
    actual_yield: float | None = Field(None, ge=0)
    yield_unit: str = Field("kg/ha", max_length=20)
    harvest_at: str | None = Field(None, max_length=30)


class MeasurementsIn(BaseModel):
    """Sensor contract measurement block (physical sensor bounds)."""
    ph: float = Field(ge=0, le=14)
    nitrogen: float = Field(ge=0, le=1000, description="kg/ha")
    phosphorus: float = Field(ge=0, le=1000, description="kg/ha")
    potassium: float = Field(ge=0, le=1000, description="kg/ha")
    moisture: float = Field(ge=0, le=100, description="%")
    temperature: float = Field(ge=-40, le=80, description="°C")


class LocationIn(BaseModel):
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class HeartbeatIn(BaseModel):
    battery: float | None = Field(None, ge=0, le=100, description="simulated %")
    signal: float | None = Field(None, ge=-120, le=0, description="simulated dBm")


class ReadingIn(BaseModel):
    """Standardized soil-reading contract — identical for the simulator
    today and the future ESP32/PCB device."""
    deviceId: str = Field(min_length=1, max_length=40)
    timestamp: str = Field(min_length=1, max_length=40, description="ISO-8601")
    measurements: MeasurementsIn
    units: dict[str, str] | None = None
    location: LocationIn | None = None
    heartbeat: HeartbeatIn | None = None


class DeviceIn(BaseModel):
    device_id: str | None = Field(
        None, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$"
    )
    name: str = Field(min_length=1, max_length=100)
    plot_id: int = Field(ge=1)
    sensors: list[str] | None = Field(None, max_length=20)
    firmware: str = Field("0.1.0", max_length=20)


class ReadingReportIn(BaseModel):
    crop: str | None = Field(None, max_length=50)
    lang: str = "en"


class SimulationIn(BaseModel):
    action: Literal["start", "stop"]
    scenario: Literal["normal", "nitrogen_deficient", "acidic", "dry"] = "normal"
    interval_seconds: float = Field(5.0, ge=1, le=60)
    device_id: str | None = Field(None, max_length=40)


class DemoRunIn(BaseModel):
    """One-click reproducible demo (Run Demo button)."""
    scenario: Literal["normal", "nitrogen_deficient", "acidic", "dry"] = "normal"
    count: int = Field(20, ge=1, le=100, description="backfilled readings")
    reset: bool = Field(True, description="clear the device's readings first")


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
    return joblib.load(config.soil_model_path()), joblib.load(config.CROP_MODEL_PKL)


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
    """Real model load state (models load lazily on first use) + model version.

    ``training_capture`` is additive and read at request time: clients show the
    opt-in consent control only when the deployment actually captures samples.
    The flag is off by default, so existing clients see no behaviour change.
    """
    capture = config.training_capture_enabled()
    try:
        _models()
    except Exception:
        payload = {"status": "degraded", "models": "error",
                   "model_version": MODEL_VERSION,
                   "training_capture": capture}
    else:
        payload = {"status": "ok", "models": "loaded",
                   "model_version": MODEL_VERSION,
                   "training_capture": capture}
    if config.LLM_TELEMETRY:
        payload["llm_contract"] = contract_counters()
    return payload


@app.get("/api/crops")
def crops():
    """All crops with their real nutrient bands (dataset percentiles).

    Additive: each entry carries name_om (Afaan Oromoo display name) from
    the Ethiopian crop matrix; the legacy keys are unchanged.
    """
    climate = _crop_climate()
    matrix = ethio.load_crop_matrix()
    return {
        "crops": [
            {
                "name": c,
                "scientific_name": SCIENTIFIC_NAMES.get(c),
                "name_om": (matrix.get(c) or {}).get("name_om"),
                "bands": get_bands(c),
                "bands_source": CROP_BANDS_SOURCE,
                "temperature": climate[c]["temperature"],
                "rainfall": climate[c]["rainfall"],
            }
            for c in available_crops()
        ]
    }


@app.get("/api/crops/ethiopian")
def ethiopian_crops():
    """Full Ethiopian crop suitability matrix (34 crops, EthioSIS-aligned).

    Additive to /api/crops: every entry carries S1/S2/N bands for pH,
    temperature and rainfall plus Amharic name and regions. 12 staples are
    rule-only (in_ml_model=false); the 22 ML crops are also served here with
    in_ml_model=true. The 22-crop ML model contract is unchanged.
    """
    matrix = ethio.load_crop_matrix()
    ml_keys = ethio.ml_crop_keys()
    return {
        "crops": [
            {
                "name": name,
                "in_ml_model": name in ml_keys,
                **entry,
            }
            for name, entry in sorted(matrix.items())
        ]
    }


@app.post("/api/crops/suitability")
def evaluate_suitability(req: SuitabilityRequest):
    """Rule-based land-suitability classes for Ethiopian crops.

    Evaluates only the provided parameters (pH/temperature/rainfall) against
    the S1/S2/N bands in the crop matrix — worst parameter wins, strict
    N-exclusions. Defaults to the 12 Ethiopian staples; pass crops=[...] to
    evaluate any matrix crop. Returns class S1/S2/S3/N plus human-readable
    reasons per crop.
    """
    if req.ph is None and req.temperature is None and req.rainfall is None:
        raise HTTPException(422, "Provide at least one of ph, temperature, rainfall")

    if req.crops is None:
        names = ethio.ethiopian_staples()
    else:
        unknown = [c for c in req.crops if c not in ethio.load_crop_matrix()]
        if unknown:
            raise HTTPException(
                400,
                f"Unknown crops: {', '.join(unknown)}. See /api/crops/ethiopian",
            )
        names = req.crops

    results = [
        ethio.crop_suitability(
            name, ph=req.ph, temperature=req.temperature, rainfall=req.rainfall
        )
        for name in names
    ]
    return {"results": [r for r in results if r]}


@app.get("/api/varieties")
def varieties(crop: str | None = Query(None, max_length=60),
              region: str | None = Query(None, max_length=100)):
    """Ethiopian crop x production-area x variety target bands.

    Additive reference endpoint. Returns the explicit variety lookup used by
    /api/predict when the optional `variety` field is supplied. Values are in
    model units (N/P/K kg/ha over 0-20 cm; pH in 1:2.5 water) and are
    advisory targets, not measured soil observations. Filters accept crop and
    region aliases; unknown filters return an empty list.
    """
    entries = variety_bands.list_varieties(crop=crop, region=region)
    return {
        "varieties": entries,
        "meta": {
            "review_status": variety_bands.meta().get("review_status"),
            "sources": variety_bands.meta().get("sources", []),
        },
    }


@app.get("/api/seasons")
def seasons(crop: str | None = Query(None, max_length=60)):
    """Ethiopian Meher/Belg N-band reference data.

    Additive reference endpoint. `seasons` always lists the canonical seasons;
    `crops` lists crops with seasonal records. Entries are the resolved
    crop x season rows used by /api/predict (N seasonal, P/K/pH baseline).
    Values are advisory targets in model units, and the dataset is marked
    unverified pending local calibration.
    """
    season_ids = seasonal_bands.list_seasons()
    season_list = []
    for season_id in season_ids:
        info = seasonal_bands.season_info(season_id)
        season_list.append({
            "id": season_id,
            "label": info.get("label") or season_id.title(),
            "months": info.get("months"),
            "note": info.get("note"),
        })
    crops = seasonal_bands.season_crops()
    crop_ids = [crop] if crop else crops
    entries = []
    for crop_id in crop_ids:
        for season_id in season_ids:
            entry = seasonal_bands.resolve_seasonal_bands(crop_id, season_id)
            if entry is not None:
                entries.append(entry)
    return {
        "seasons": season_list,
        "crops": crops,
        "entries": entries,
        "meta": seasonal_bands.meta(),
    }


@app.get("/api/soil-conditioning")
def soil_conditioning_reference():
    """SOC/texture conditioning classes, rules and metadata.

    Additive reference endpoint for Enhancement E. These are unverified
    advisory rules, not measured observations; the predictor only applies
    them when texture/SOC/woreda inputs are supplied (or the env flag is on).
    """
    return soil_conditioning.reference()


def _prepare_prediction(req: PredictRequest):
    """Validate inputs and compute the shared prediction context.

    Kept separate from response assembly so the SSE endpoint can stream the
    same analysis without duplicating the Ethiopian zone/variety/season logic.
    """
    variety_match = None
    if req.variety:
        if not req.crop:
            raise HTTPException(
                400, "crop is required when a variety is supplied")
        variety_match = variety_bands.resolve_variety(
            req.crop, region=req.region, variety=req.variety)
        if variety_match is None:
            options = variety_bands.list_varieties(
                crop=req.crop, region=req.region)
            hint = ", ".join(
                f"{entry['variety']} ({entry['region_label']})"
                for entry in options
            ) or "see /api/varieties"
            raise HTTPException(
                400,
                f"Unknown variety '{req.variety}' for crop '{req.crop}'"
                + (f" in region '{req.region}'" if req.region else "")
                + f". Valid: {hint}",
            )

    season_entry = None
    if req.season:
        if not req.crop:
            raise HTTPException(
                400, "crop is required when a season is supplied")
        if seasonal_bands.canonical_season(req.season) is None:
            raise HTTPException(
                400,
                f"Unknown season '{req.season}'. Valid: "
                f"{', '.join(seasonal_bands.list_seasons())}",
            )
        seasonal_crop = variety_match["crop"] if variety_match else req.crop
        season_entry = seasonal_bands.resolve_seasonal_bands(
            seasonal_crop, req.season)
        if season_entry is None:
            raise HTTPException(
                400,
                f"Season '{req.season}' is not available for crop "
                f"'{seasonal_crop}'. Seasonal crops: "
                f"{', '.join(seasonal_bands.season_crops())}",
            )

    if (req.crop and req.crop.lower() not in available_crops()
            and variety_match is None and season_entry is None):
        raise HTTPException(400, f"Unknown crop '{req.crop}'. See /api/crops")

    zone = ethio.canonical_zone(req.agroecological_zone)
    if req.agroecological_zone and zone is None:
        raise HTTPException(
            400,
            f"Unknown agroecological zone '{req.agroecological_zone}'. "
            f"Valid: {', '.join(ethio.list_zones())}",
        )

    soil_type = ethio.canonical_soil_type(req.soil_type) if req.soil_type else None
    if req.soil_type and soil_type is None:
        raise HTTPException(
            400,
            f"Unknown soil type '{req.soil_type}'. "
            f"Valid: {', '.join(ethio.list_soil_types())}",
        )
    lime_type = req.lime_type or ethio.DEFAULT_LIME_TYPE

    texture_class = (
        soil_conditioning.canonical_texture(req.texture_class)
        if req.texture_class else None
    )
    if req.texture_class and texture_class is None:
        valid = ", ".join(
            item["id"] for item in soil_conditioning.list_texture_classes())
        raise HTTPException(
            400,
            f"Unknown texture class '{req.texture_class}'. Valid: {valid}",
        )
    soc_class = (
        soil_conditioning.canonical_soc_class(req.soc_class)
        if req.soc_class else None
    )
    if req.soc_class and soc_class is None:
        valid = ", ".join(
            item["id"] for item in soil_conditioning.list_soc_classes())
        raise HTTPException(
            400,
            f"Unknown SOC class '{req.soc_class}'. Valid: {valid}",
        )
    if req.soc_pct is not None:
        soc_class = soil_conditioning.soc_class_for_pct(req.soc_pct)

    raw_lang = (req.lang or "").strip().lower()
    if raw_lang not in (
        "en", "english", "am", "amh", "am-et", "amharic", "አማርኛ",
        "om", "orm", "om-et", "oromo", "afaan oromoo", "afaan oromia",
        "oromiffa", "qubee",
    ):
        raise HTTPException(422, "lang must be one of: en, am, om")
    lang = normalize_lang(req.lang)

    units = req.input_units or "kg_ha"
    if units == "lab":
        N = ethio.n_pct_to_kg_ha(req.N)
        P = ethio.p_mgkg_to_kg_ha(req.P)
        K = ethio.k_cmolc_to_kg_ha(req.K)
    else:
        N, P, K = req.N, req.P, req.K
    ph = req.ph

    soil_rf, crop_rf = _models()

    # The agroecology model carries one-hot zone columns (feature_cols_);
    # the legacy model is the plain N/P/K/ph schema. The v6 model also
    # declares soil_type_<name> columns; build_soil_row drops soil_type
    # for every other schema, so passing it is always safe.
    soil_row = pd.DataFrame([{"N": N, "P": P, "K": K, "ph": ph, "zone": zone,
                              "soil_type": soil_type}])
    soil_row = build_soil_row(soil_row, soil_rf)
    fertility_status = soil_rf.predict(soil_row)[0]
    proba = soil_rf.predict_proba(soil_row)[0]
    predicted_idx = list(soil_rf.classes_).index(fertility_status)
    confidence = round(float(proba[predicted_idx]) * 100, 1)

    # Catalog product selection (flag off → the legacy DAP/K2SO4 trio; the
    # S/B/region inputs are recorded but change nothing).
    selection = fertilizer_catalog.resolve_selection(
        crop=req.crop, sulfur_ppm=req.sulfur_ppm,
        boron_ppm=req.boron_ppm, region=req.region,
    )

    effective_crop = variety_match["crop"] if variety_match else req.crop
    analysis = analyze_soil(
        N, P, K, ph, crop=effective_crop, zone=zone,
        ec_e=req.ec_e, esp=req.esp,
        soil_type=soil_type, lime_type=lime_type,
        variety=req.variety, region=req.region, season=req.season,
        texture_class=texture_class, soc_pct=req.soc_pct,
        soc_class=soc_class, woreda=req.woreda,
    )
    deterministic = recommend_soil(analysis, lang=lang, selection=selection,
                                   zone=zone)

    return {
        "req": req,
        "variety_match": variety_match,
        "season_entry": season_entry,
        "zone": zone,
        "soil_type": soil_type,
        "lime_type": lime_type,
        "lang": lang,
        "units": units,
        "N": N,
        "P": P,
        "K": K,
        "ph": ph,
        "analysis": analysis,
        "deterministic": deterministic,
        "selection": selection,
        "crop_rf": crop_rf,
        "fertility_status": fertility_status,
        "confidence": confidence,
    }


def _format_recommendations(ctx, merged):
    req = ctx["req"]
    analysis = ctx["analysis"]
    lang = ctx["lang"]
    selection = ctx["selection"]
    recommendations = []
    for i, r in enumerate(merged):
        rec = {
            "element": r["element"],
            "status": r["status"],
            "deficit": r["deficit"],
            "value": analysis[i]["value"],
            "ideal_range": _ideal_range_string(analysis[i]),
            "solution": r["solution"],
            "source": r["source"],
            "lang": lang,
        }
        if req.include_application_guide:
            product = product_for_rec(analysis[i]["col"], r["status"],
                                      selection=selection)
            rec["application"] = application_schedule(
                product, crop=req.crop, lang=lang)
        recommendations.append(rec)
    return recommendations


def _build_prediction_response(ctx, llm_recs=None):
    req = ctx["req"]
    analysis = ctx["analysis"]
    lang = ctx["lang"]
    selection = ctx["selection"]
    zone = ctx["zone"]
    soil_type = ctx["soil_type"]
    lime_type = ctx["lime_type"]
    N, P, K, ph = ctx["N"], ctx["P"], ctx["K"], ctx["ph"]

    merged = ctx["deterministic"]
    if llm_recs:
        merged = merge_recommendations(
            llm_recs, ctx["deterministic"], analysis=analysis, lang=lang,
            selection=selection,
        )
    recommendations = _format_recommendations(ctx, merged)

    climate = _climate_defaults()
    row = pd.DataFrame(
        [{
            "N": N, "P": P, "K": K,
            "temperature": req.temperature if req.temperature is not None else climate["temperature"],
            "humidity": req.humidity if req.humidity is not None else climate["humidity"],
            "ph": ph,
            "rainfall": req.rainfall if req.rainfall is not None else climate["rainfall"],
        }]
    )[config.CROP_FEATURES]
    crop_recs = _top_crops(ctx["crop_rf"], row)

    # Land-suitability overlay — only when the user supplied the climate
    # (dataset medians would misfire, e.g. Kaggle rice rainfall ~200mm vs
    # matrix 1100-1600). Additive field; the ML contract is unchanged.
    if req.temperature is not None and req.rainfall is not None:
        for rec in crop_recs:
            suit = ethio.crop_suitability(
                rec["crop"], ph=ph, temperature=req.temperature, rainfall=req.rainfall
            )
            if suit:
                rec["suitability"] = {"class": suit["class"], "reasons": suit["reasons"]}

    # Additive Phase 2 blocks — only when the inputs that drive them were
    # supplied (or catalog mode is explicitly on), so a default request keeps
    # the exact legacy response shape.
    extras = {}
    micronutrients = micronutrient_check(req.sulfur_ppm, req.boron_ppm,
                                         lang=lang)
    if micronutrients is not None:
        extras["micronutrients"] = micronutrients
    if (req.sulfur_ppm is not None or req.boron_ppm is not None
            or req.region or fertilizer_catalog.catalog_mode_enabled()):
        extras["product_selection"] = product_selection_block(
            selection, crop=req.crop, sulfur_ppm=req.sulfur_ppm,
            boron_ppm=req.boron_ppm, region=req.region, lang=lang,
        )

    conditioning_meta = next(
        (item.get("soil_conditioning") for item in analysis
         if item.get("soil_conditioning")), None)
    if conditioning_meta is not None:
        extras["soil_conditioning"] = {**conditioning_meta, "lang": lang}

    if zone:
        info = ethio.zone_info(zone)
        bands = ethio.zone_advice_ranges(zone)
        agro = {
            "zone": zone,
            "name": ethio.zone_display_name(zone, lang),
            "bands": {col: [lo, hi] for col, (lo, hi) in bands.items()},
            "constraint": info["constraint"],
            "source": info["source"],
        }
        overrides = ethio.zone_overrides(zone) or {}
        pause = overrides.get("ec_e_pause_ds_m")
        if req.ec_e is not None and pause is not None:
            agro["salinity"] = {
                "ec_e_ds_m": req.ec_e,
                "pause_threshold_ds_m": pause,
                "fertilizer_paused": req.ec_e > pause,
                "esp": req.esp,
                "sodic_threshold_pct": overrides.get("esp_sodic_above"),
                "sodic": (req.esp is not None
                          and req.esp > overrides.get("esp_sodic_above", 1e9)),
                "reclamation_product": overrides.get("reclamation_product"),
            }
        extras["agroecology"] = agro

    if soil_type:
        ph_item = next(
            (i for i in analysis if i.get("col") == "ph"), None)
        if ph_item is not None and ph_item["status"] == "deficient":
            plan = ethio.lime_plan(
                ph_item["deficit"], soil_type, lime_type)
            if plan is not None:
                plan["lang"] = lang
                extras["lime_plan"] = plan

    season_entry = ctx["season_entry"]
    if season_entry is not None:
        n_item = next(
            (i for i in analysis if i.get("col") == "N"), {})
        extras["season"] = {
            "id": season_entry["id"],
            "crop": season_entry["crop"],
            "crop_label": season_entry["crop_label"],
            "season": season_entry["season"],
            "label": season_entry["season_label"],
            "months": season_entry["season_months"],
            "bands": {
                col: list(season_entry["bands"][col])
                for col in seasonal_bands.BAND_COLS
            },
            "n_band": list(season_entry["bands"]["N"]),
            "base_bands": {
                col: list(season_entry["base_bands"][col])
                for col in seasonal_bands.BASE_COLS
            },
            "n_factor": season_entry["n_factor"],
            "applied_n_source": n_item.get("season_applied_n_source"),
            "unchanged": ["P", "K", "ph"],
            "note": season_entry["crop_note"],
            "season_note": season_entry["season_note"],
            "source": season_entry["source"],
            "review_status": season_entry["review_status"],
        }

    variety_match = ctx["variety_match"]
    if variety_match is not None:
        extras["variety"] = {
            "id": variety_match["id"],
            "crop": variety_match["crop"],
            "crop_label": variety_match.get("crop_label"),
            "region": variety_match.get("region"),
            "region_label": variety_match.get("region_label"),
            "label": variety_match["variety"],
            "aliases": variety_match.get("aliases", []),
            "bands": {
                col: list(variety_match["bands"][col])
                for col in variety_bands.BAND_COLS
            },
            "source": variety_match.get("source"),
            "review_status": variety_match.get("review_status"),
            "match_level": variety_match.get("match_level"),
        }

    return {
        "fertility_status": ctx["fertility_status"],
        "confidence": ctx["confidence"],
        "lang": lang,
        "input_units": ctx["units"],
        "recommendations": recommendations,
        "crops": crop_recs,
        "warnings": training_range_warnings(N, P, K, ph, lang=lang),
        "llm_used": any(r["source"] == "llm" for r in recommendations),
        "model_version": MODEL_VERSION,
        **extras,
    }


def _llm_recs_for(req, ctx):
    """Existing non-streaming LLM path, isolated for the shared response builder."""
    try:
        # selection is forwarded only in catalog mode: the legacy call
        # signature stays exactly as before (some clients mock the
        # three-argument form), while catalog mode needs the prompt to
        # name the products the report will actually use.
        if fertilizer_catalog.catalog_mode_enabled():
            return get_llm_recommendations(
                ctx["analysis"], os.getenv("GROQ_API_KEY"), lang=ctx["lang"],
                selection=ctx["selection"],
            )
        return get_llm_recommendations(
            ctx["analysis"], os.getenv("GROQ_API_KEY"), lang=ctx["lang"]
        )
    except Exception:
        return None


@app.post("/api/predict")
def predict(req: PredictRequest):
    """Predict fertility, per-nutrient recommendations and top-3 crops.

    Returns the same shape the UI renders: fertility_status, confidence,
    recommendations[{element, status, deficit, value, ideal_range,
    solution, source}], crops[{crop, scientific_name, confidence,
    temperature, rainfall}] — plus training-range warnings[], llm_used,
    input_units and model_version. Set use_llm=true for LLM-enriched
    solutions (validated against the computed analysis; always falls back
    to rule-based). With input_units=lab, N/P/K arrive in EthioSIS/ESRI
    lab units (total N %, available P mg/kg, exchangeable K cmolc/kg) and
    are converted to kg/ha internally; responses stay in model units.
    When temperature+rainfall are provided, each crop entry gains an
    additive suitability {class, reasons} (S1/S2/S3/N from the Ethiopian
    crop matrix). sulfur_ppm/boron_ppm/region feed the catalog product
    selection (NPS/NPSB/region blend when FERT_PRODUCT_CATALOG=on) and add
    response-level micronutrients/product_selection blocks; they never
    change the legacy keys. include_application_guide=true adds an
    'application' growth-stage schedule per recommendation.
    agroecological_zone=<id> switches N/P/K/pH analysis to that Ethiopian
    zone's EIAR bands (replacing generic and crop bands) and adds an
    additive 'agroecology' response block {zone, name, bands, constraint,
    source}; without it the response shape is unchanged. Zone-specific
    agronomy also applies: lime-first below pH 5.5 (acid highlands, with P
    fixation scaling below pH 5.0), split N on Vertisols (K fertilizer
    bypassed above 400 kg/ha soil K), NPSZnB basal blend on the Rift Valley
    above pH 7.5. ec_e (dS/m) + esp (%) feed the arid-zone salinity rules:
    above the zone's ECe threshold fertilizer recommendations are replaced
    by gypsum/leaching guidance (additive 'agroecology.salinity' block).
    soil_type (nitisol/vertisol/cambisol_luvisol/andosol/arenosol) + lime_type
    (calcitic/dolomitic) switch the pH-deficient lime tonnage to the
    Ethiopian LRF rates (Dataset/lime_requirement.json) and add an additive
    'lime_plan' block {soil_type, lrf, total_t_ha, max_single_t_ha,
    schedule[...]} enforcing the 5.0 t/ha single-application ceiling with a
    seasonal split; without soil_type the legacy lime formula and response
    shape are unchanged.
    variety=<name/alias> (+ optional region=<production area>) switches N/P/K/pH
    to that explicit Ethiopian variety's advisory bands (precedence variety >
    zone > crop > generic) and adds an additive 'variety' block; crop is
    required, and unknown/incompatible combinations -> 400. Supplying a zone
    at the same time keeps the zone's agronomy overrides. Without variety the
    response shape is unchanged.
    texture_class/soc_pct/soc_class/woreda are the optional Enhancement E
    inputs (unverified advisory deltas to N/P/K bands); supplying any of them
    adds an additive 'soil_conditioning' block. Unknown classes -> 400;
    unknown woredas fall through silently, and no new inputs means the legacy
    response is unchanged. See /api/soil-conditioning for rules.
    """
    ctx = _prepare_prediction(req)
    llm_recs = _llm_recs_for(req, ctx) if req.use_llm else None
    return _build_prediction_response(ctx, llm_recs)


def _sse_event(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/predict/stream")
async def predict_stream(req: PredictRequest, request: Request):
    """SSE variant of /api/predict.

    Emits a deterministic snapshot first, then validated LLM elements as they
    complete, and a final merged recommendation list. The existing
    /api/predict contract is untouched; any LLM failure leaves the snapshot as
    the result. Only records that pass the same numeric/status validation as
    the non-streaming path are sent to the client.
    """
    ctx = await asyncio.to_thread(_prepare_prediction, req)
    snapshot = _build_prediction_response(ctx)

    async def event_stream():
        yield _sse_event("snapshot", snapshot)
        if not req.use_llm:
            yield _sse_event("done", {
                "recommendations": snapshot["recommendations"],
                "llm_used": snapshot["llm_used"],
                "validated": [],
                "rejected": [],
                "truncated": False,
                "reason": None,
            })
            return

        validated = {}
        rejected = []
        truncated = False
        reason = None
        stream = stream_llm_recommendations(
            ctx["analysis"],
            os.getenv("GROQ_API_KEY"),
            lang=ctx["lang"],
            selection=(ctx["selection"]
                       if fertilizer_catalog.catalog_mode_enabled()
                       else None),
        )
        try:
            async for event in stream:
                if await request.is_disconnected():
                    return
                kind = event.get("type")
                if kind == "element":
                    element = event["element"]
                    record = event["record"]
                    merged = merge_recommendations(
                        {element: record}, ctx["deterministic"],
                        analysis=ctx["analysis"], lang=ctx["lang"],
                        selection=ctx["selection"],
                    )
                    match = next(
                        (rec for rec in merged if rec["element"] == element),
                        None,
                    )
                    if match is None or match["source"] != "llm":
                        rejected.append(element)
                        continue
                    if element in validated:
                        continue
                    validated[element] = record
                    formatted = _format_recommendations(ctx, merged)
                    rec = next(
                        (item for item in formatted
                         if item["element"] == element),
                        None,
                    )
                    if rec is not None:
                        yield _sse_event("element", rec)
                elif kind == "done":
                    truncated = bool(event.get("truncated"))
                    reason = event.get("reason")
        except Exception:
            reason = "stream_error"
            yield _sse_event("error", {
                "message": "Streaming failed; showing calculated "
                           "recommendations.",
                "reason": reason,
            })
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass

        if await request.is_disconnected():
            return
        final = _build_prediction_response(ctx, validated or None)
        yield _sse_event("done", {
            "recommendations": final["recommendations"],
            "llm_used": final["llm_used"],
            "validated": sorted(validated),
            "rejected": sorted(set(rejected)),
            "truncated": truncated,
            "reason": reason,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/reports")
def reports(plot: str | None = Query(None), limit: int = Query(20, ge=1, le=100),
            farmer_id: int | None = Query(None, ge=1)):
    """Saved report history, newest first. Optionally filter by plot/farmer."""
    return {"reports": db.list_reports(plot=plot, limit=limit, farmer_id=farmer_id)}


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
    if report.farmer_id is not None and db.get_farmer(report.farmer_id) is None:
        raise HTTPException(404, f"Farmer {report.farmer_id} not found")
    if report.plot_id is not None:
        plot = db.get_plot(report.plot_id)
        if plot is None:
            raise HTTPException(404, f"Plot {report.plot_id} not found")
        if report.farmer_id is not None and plot["farmer_id"] != report.farmer_id:
            raise HTTPException(400, "plot does not belong to farmer")
    if report.agroecological_zone and not ethio.zone_exists(report.agroecological_zone):
        raise HTTPException(
            400,
            f"Unknown agroecological zone '{report.agroecological_zone}'. "
            f"Valid: {', '.join(ethio.list_zones())}",
        )
    if report.variety and (
            not report.crop
            or variety_bands.resolve_variety(
                report.crop, region=report.region, variety=report.variety
            ) is None):
        raise HTTPException(
            400,
            f"Unknown variety '{report.variety}' for crop '{report.crop}'"
            + (f" in region '{report.region}'" if report.region else "")
            + ". See /api/varieties",
        )
    if report.season:
        if (not report.crop
                or seasonal_bands.resolve_seasonal_bands(
                    report.crop, report.season) is None):
            raise HTTPException(
                400,
                f"Season '{report.season}' is not available for crop "
                f"'{report.crop}'. Seasonal crops: "
                f"{', '.join(seasonal_bands.season_crops())}",
            )
    texture_class = (
        soil_conditioning.canonical_texture(report.texture_class)
        if report.texture_class else None
    )
    if report.texture_class and texture_class is None:
        raise HTTPException(
            400, f"Unknown texture class '{report.texture_class}'. See "
                 f"/api/soil-conditioning",
        )
    soc_class = (
        soil_conditioning.canonical_soc_class(report.soc_class)
        if report.soc_class else None
    )
    if report.soc_class and soc_class is None:
        raise HTTPException(
            400, f"Unknown SOC class '{report.soc_class}'. See "
                 f"/api/soil-conditioning",
        )
    if report.soc_pct is not None:
        soc_class = soil_conditioning.soc_class_for_pct(report.soc_pct)
    soil_type = (
        ethio.canonical_soil_type(report.soil_type)
        if report.soil_type else None
    )
    if report.soil_type and soil_type is None:
        raise HTTPException(
            400,
            f"Unknown soil type '{report.soil_type}'. "
            f"Valid: {', '.join(ethio.list_soil_types())}",
        )
    report_id = db.save_report(
        report.plot, report.N, report.P, report.K, report.ph,
        report.fertility_status, report.recommendations,
        crop=report.crop, temperature=report.temperature,
        humidity=report.humidity, rainfall=report.rainfall, crops=report.crops,
        lang=normalize_lang(report.lang),
        farmer_id=report.farmer_id, plot_id=report.plot_id, source=report.source,
        sulfur_ppm=report.sulfur_ppm, boron_ppm=report.boron_ppm,
        region=report.region, variety=report.variety,
        season=seasonal_bands.canonical_season(report.season),
        agroecological_zone=ethio.canonical_zone(report.agroecological_zone)
        or report.agroecological_zone,
        ec_e=report.ec_e, esp=report.esp,
        texture_class=texture_class, soc_pct=report.soc_pct,
        soc_class=soc_class, woreda=report.woreda,
    )
    # Opt-in, best-effort training capture. This never changes the report
    # response and never uses the model's own fertility_status as a label.
    if report.consent_for_training and config.training_capture_enabled():
        try:
            db.save_training_sample(
                report.N, report.P, report.K, report.ph,
                report_id=report_id, source=report.source, consent=True,
                crop=report.crop, variety=report.variety,
                region=report.region,
                season=seasonal_bands.canonical_season(report.season),
                agroecological_zone=ethio.canonical_zone(
                    report.agroecological_zone) or report.agroecological_zone,
                soil_type=soil_type, texture_class=texture_class,
                soc_pct=report.soc_pct, soc_class=soc_class,
                woreda=report.woreda,
            )
        except Exception:
            pass
    return {"id": report_id, "saved": True}


@app.get("/api/reports/{report_id}")
def get_report_detail(report_id: int):
    """One saved report with its outcome and farmer/plot context.

    Serves the printable report view: parsed recommendations, linked outcome,
    and the farmer/plot identity (names, region, farm size) in one call.

    Recommendations saved by older writers (the Streamlit UI, seed_demo) omit
    `value`/`ideal_range`; they are recomputed here from the stored N/P/K/ph so
    the printable view always has numbers without needing a migration.
    """
    report = db.get_report(report_id)
    if report is None:
        raise HTTPException(404, f"Report {report_id} not found")
    _fill_missing_recommendation_metrics(report)
    farmer = (
        db.get_farmer(report["farmer_id"]) if report.get("farmer_id") else None
    )
    plot = db.get_plot(report["plot_id"]) if report.get("plot_id") else None
    return {
        "report": report,
        "outcome": db.get_outcome(report_id),
        "farmer": farmer,
        "plot": plot,
    }


def _has_recommendation_metrics(rec):
    return "value" in rec and "ideal_range" in rec


def _fill_missing_recommendation_metrics(report):
    """Add `value`/`ideal_range` to stored recommendations that lack them."""
    recommendations = report.get("recommendations") or []
    if not recommendations or all(_has_recommendation_metrics(r) for r in recommendations):
        return
    analysis = analyze_soil(
        report["N"], report["P"], report["K"], report["ph"],
        crop=report.get("crop"),
        zone=report.get("agroecological_zone"),
        variety=report.get("variety"),
        region=report.get("region"),
        season=report.get("season"),
        texture_class=report.get("texture_class"),
        soc_pct=report.get("soc_pct"),
        soc_class=report.get("soc_class"),
        woreda=report.get("woreda"),
    )
    by_element = {item["element"]: item for item in analysis}
    for rec in recommendations:
        if _has_recommendation_metrics(rec):
            continue
        item = by_element.get(rec.get("element"))
        if item is None:
            continue
        rec.setdefault("value", item["value"])
        rec.setdefault("ideal_range", _ideal_range_string(item))


@app.get("/api/reports/{report_id}/cost-estimate")
def report_cost_estimate(
    report_id: int,
    soil_type: str | None = Query(
        None, max_length=40,
        description="Optional Ethiopian soil type (LRF): nitisol, vertisol, "
        "cambisol_luvisol, andosol, arenosol. Prices the total lime program "
        "at the soil-type rate; omitted -> legacy generic lime formula",
    ),
    lime_type: Literal["calcitic", "dolomitic"] = Query(
        "calcitic",
        description="Optional lime product; dolomitic applies the x0.84 "
        "neutralizing-value adjustment (requires soil_type to matter)",
    ),
):
    """Indicative fertilizer cost estimate for one saved report.

    Recomputed from the stored N/P/K/pH/crop (never from stale recommendation
    text) in ETB per hectare, plus a plot total when the report is linked to a
    plot with a known farm size. Prices are demo placeholders — see the
    response `disclaimer`. The optional soil_type/lime_type query params
    price the Ethiopian LRF lime program (total across seasons); without
    them the legacy lime formula applies.
    """
    report = db.get_report(report_id)
    if report is None:
        raise HTTPException(404, f"Report {report_id} not found")
    canonical_soil = None
    if soil_type:
        canonical_soil = ethio.canonical_soil_type(soil_type)
        if canonical_soil is None:
            raise HTTPException(
                400,
                f"Unknown soil type '{soil_type}'. "
                f"Valid: {', '.join(ethio.list_soil_types())}",
            )
    size_ha = None
    if report.get("plot_id"):
        plot = db.get_plot(report["plot_id"])
        if plot:
            size_ha = plot.get("farm_size_ha")
    return fertilizer_cost.estimate(
        report["N"], report["P"], report["K"], report["ph"],
        crop=report.get("crop"), size_ha=size_ha,
        sulfur_ppm=report.get("sulfur_ppm"),
        boron_ppm=report.get("boron_ppm"),
        region=report.get("region"),
        variety=report.get("variety"),
        season=report.get("season"),
        zone=report.get("agroecological_zone"),
        ec_e=report.get("ec_e"),
        esp=report.get("esp"),
        soil_type=canonical_soil,
        lime_type=lime_type,
        texture_class=report.get("texture_class"),
        soc_pct=report.get("soc_pct"),
        soc_class=report.get("soc_class"),
        woreda=report.get("woreda"),
    )


@app.get("/api/fertilizer/products")
def fertilizer_products():
    """Ethiopian fertilizer product specifications + indicative prices.

    Spec sheet for the blends used in Ethiopia (NPS, NPSB, Urea, DAP,
    region blends): N-P2O5-K2O plus S/B/Zn contents (nominal and range),
    application timing and restrictions, decision rules (e.g. S < 20 ppm
    with sufficient B → NPS over DAP), target regions/crops, and
    indicative co-op vs open-market ETB per 50 kg bag with derived
    per-kg ranges. Sources: EthioSIS atlas, ISFM manual, Fertilizer &
    Soil Health Roadmap 2026-2035. Pure reference data — does not change
    any recommendation.
    """
    catalog = fertilizer_catalog.load_catalog()
    return {
        "products": fertilizer_catalog.get_products(),
        "currency": catalog.get("currency", "ETB"),
        "bag_kg": catalog.get("bag_kg", 50),
        "price_note": catalog.get("price_note"),
        "source_documents": catalog.get("source_documents", []),
        "disclaimer": fertilizer_cost.DISCLAIMER,
        "disclaimer_key": fertilizer_cost.DISCLAIMER_KEY,
    }


# ---------------- Farmers / plots / outcomes (agent flow) ----------------


@app.post("/api/farmers")
def create_farmer(farmer: FarmerIn):
    farmer_id = db.create_farmer(
        farmer.name, phone=farmer.phone, region=farmer.region,
        lang=normalize_lang(farmer.lang),
    )
    return {"id": farmer_id, "farmer": db.get_farmer(farmer_id)}


@app.get("/api/farmers")
def list_farmers():
    """All farmers with plot/report counts, newest first."""
    return {"farmers": db.list_farmer_summaries()}


@app.get("/api/farmers/{farmer_id}")
def farmer_detail(farmer_id: int):
    """Full agent profile: farmer + plots (with statuses) + reports (with outcomes)."""
    detail = db.farmer_detail(farmer_id)
    if detail is None:
        raise HTTPException(404, f"Farmer {farmer_id} not found")
    return detail


@app.post("/api/plots")
def create_plot(plot: PlotIn):
    if db.get_farmer(plot.farmer_id) is None:
        raise HTTPException(404, f"Farmer {plot.farmer_id} not found")
    plot_id = db.create_plot(
        plot.farmer_id, plot.name, farm_size_ha=plot.farm_size_ha,
        latitude=plot.latitude, longitude=plot.longitude,
        previous_crop=plot.previous_crop,
    )
    return {"id": plot_id, "plot": db.get_plot(plot_id)}


@app.post("/api/reports/{report_id}/outcome")
def save_outcome(report_id: int, outcome: OutcomeIn):
    if db.get_report(report_id) is None:
        raise HTTPException(404, f"Report {report_id} not found")
    row = db.upsert_outcome(
        report_id,
        implementation_notes=outcome.implementation_notes,
        implemented_at=outcome.implemented_at,
        expected_yield=outcome.expected_yield,
        actual_yield=outcome.actual_yield,
        yield_unit=outcome.yield_unit,
        harvest_at=outcome.harvest_at,
    )
    return {"id": row["id"], "saved": True, "outcome": row}


@app.get("/api/reports/{report_id}/outcome")
def get_outcome(report_id: int):
    if db.get_report(report_id) is None:
        raise HTTPException(404, f"Report {report_id} not found")
    return {"outcome": db.get_outcome(report_id)}


# ---------------- IoT: ingestion, devices, live simulation (Phase 2) ----------------


def _device_view(device, include_latest=True):
    """Device row without its token, plus derived status and plot binding."""
    view = {
        key: device[key]
        for key in (
            "id", "device_id", "name", "plot_id", "sensors", "firmware",
            "battery", "signal", "last_seen_at", "created_at",
        )
    }
    view.update(iot.device_status(device))
    plot = db.get_plot(device["plot_id"])
    view["plot_name"] = plot["name"] if plot else None
    view["farmer_id"] = plot["farmer_id"] if plot else None
    if include_latest:
        view["latest_reading"] = db.latest_reading(device["id"])
    return view


def _assessment(reading, lang):
    """Run the live fertility pipeline on a stored reading.

    Reuses predict() directly — same model, engine, warnings and crop
    matches as a manual analysis, no duplicated logic.
    """
    result = predict(PredictRequest(
        N=reading["N"], P=reading["P"], K=reading["K"], ph=reading["ph"],
        temperature=reading["temperature"], humidity=None, rainfall=None,
        lang=lang,
    ))
    return {
        "reading_id": reading["id"],
        "reading_ts": reading["reading_ts"],
        "source": reading["source"],
        "simulated": reading["source"] == "iot_simulator",
        **result,
    }


@app.post("/api/iot/readings")
def ingest_reading(req: ReadingIn, x_device_token: str | None = Header(None)):
    """Device-facing ingest — the exact contract the future ESP32 will call.

    Requires X-Device-Token. Auth runs before the device lookup (401 for both
    unknown device and wrong token — no ID enumeration), contract violations
    are rejected, suspicious-but-possible readings are stored with quality
    flags instead of dropped, and replays of the same (device, timestamp)
    return the original id with `duplicate: true` without writing a second row.
    """
    try:
        reading_id, quality, flags, duplicate = iot.ingest_reading(
            req.model_dump(), x_device_token
        )
    except iot.IngestError as exc:
        raise HTTPException(exc.status, exc.detail)
    return {
        "id": reading_id,
        "saved": True,
        "quality": quality,
        "flags": flags,
        "duplicate": duplicate,
    }


@app.get("/api/iot/readings")
def list_iot_readings(
    device_id: str = Query(..., min_length=1, max_length=40),
    limit: int = Query(30, ge=1, le=200),
):
    """Recent readings for a device, newest first (fuels the trend charts)."""
    device = db.get_device(device_id)
    if device is None:
        raise HTTPException(404, f"Device '{device_id}' not found")
    return {"readings": db.list_readings(device["id"], limit=limit)}


@app.post("/api/iot/devices")
def register_device(req: DeviceIn):
    """Register a soil sensor device (virtual today, physical later).

    Returns the one-time device token — the client must store it and send
    it as X-Device-Token on every reading.
    """
    if db.get_plot(req.plot_id) is None:
        raise HTTPException(404, f"Plot {req.plot_id} not found")
    device_id = req.device_id or iot.generate_device_id()
    if db.get_device(device_id) is not None:
        raise HTTPException(409, f"Device '{device_id}' already exists")
    token = iot.generate_token()
    row_id = db.register_device(
        device_id, req.name, req.plot_id, token,
        sensors=req.sensors, firmware=req.firmware,
    )
    device = db.get_device(device_id)
    return {"id": row_id, "device_id": device_id, "token": token,
            "device": _device_view(device)}


@app.get("/api/iot/devices")
def list_iot_devices():
    """All devices with derived status (online/offline/never_seen) + latest reading."""
    return {"devices": [_device_view(d) for d in db.list_devices()]}


@app.get("/api/iot/devices/{device_id}")
def get_iot_device(device_id: str):
    device = db.get_device(device_id)
    if device is None:
        raise HTTPException(404, f"Device '{device_id}' not found")
    return {"device": _device_view(device)}


@app.get("/api/iot/devices/{device_id}/assessment")
def device_assessment(device_id: str, lang: str = Query("en")):
    """Live soil intelligence for a device's latest reading: fertility status,
    recommendations, crop matches and training-range warnings."""
    device = db.get_device(device_id)
    if device is None:
        raise HTTPException(404, f"Device '{device_id}' not found")
    reading = db.latest_reading(device["id"])
    if reading is None:
        raise HTTPException(404, f"Device '{device_id}' has no readings yet")
    return {"device_id": device_id, **_assessment(reading, lang)}


@app.post("/api/iot/readings/{reading_id}/report")
def reading_to_report(reading_id: int, req: ReadingReportIn):
    """Persist one IoT reading into the soil-test history (Phase 1 report).

    farmer/plot come from the device's binding; source stays
    `iot_simulator` so history never pretends simulated data was manual.
    """
    reading = db.get_reading(reading_id)
    if reading is None:
        raise HTTPException(404, f"Reading {reading_id} not found")
    plot = db.get_plot(reading["plot_id"])
    if plot is None:
        raise HTTPException(400, "Device's plot no longer exists")
    if req.crop and req.crop.lower() not in available_crops():
        raise HTTPException(400, f"Unknown crop '{req.crop}'. See /api/crops")

    result = predict(PredictRequest(
        N=reading["N"], P=reading["P"], K=reading["K"], ph=reading["ph"],
        temperature=reading["temperature"], humidity=None, rainfall=None,
        crop=req.crop, lang=req.lang,
    ))
    report_id = db.save_report(
        plot["name"], reading["N"], reading["P"], reading["K"], reading["ph"],
        result["fertility_status"], result["recommendations"],
        crop=req.crop, temperature=reading["temperature"],
        crops=result["crops"], lang=normalize_lang(req.lang),
        farmer_id=plot["farmer_id"], plot_id=plot["id"],
        source=reading["source"],
    )
    return {"id": report_id, "saved": True,
            "farmer_id": plot["farmer_id"], "report": db.get_report(report_id)}


def _require_control_token(x_control_token: str | None = Header(None)):
    """Opt-in auth for demo-control endpoints (F3): simulation start/stop and
    Run Demo.

    Demo default — `IOT_CONTROL_TOKEN` unset — stays open so the dashboard's
    simulator controls keep working with zero configuration. Operators who
    set the env var require a matching `X-Control-Token` header (401
    otherwise). The token value is never logged.
    """
    expected = os.getenv("IOT_CONTROL_TOKEN")
    if expected and not (
        x_control_token
        and secrets.compare_digest(str(x_control_token), str(expected))
    ):
        raise HTTPException(401, "missing or invalid X-Control-Token")


@app.post("/api/iot/simulation")
async def control_simulation(req: SimulationIn,
                             x_control_token: str | None = Header(None)):
    """Start/stop (or reconfigure) the in-process IoT simulator.

    The simulator walks the bound device's values through the chosen
    scenario using the same quality pipeline as real device traffic.
    Guarded by IOT_CONTROL_TOKEN when configured (F3) and by the demo-run
    slot: starting while a Run Demo backfill is writing would create two
    writers for one device (F4).
    """
    _require_control_token(x_control_token)
    if req.action == "stop":
        iot.stop_simulation()
        return {"simulation": iot.simulation_state()}

    if iot.demo_run_active():
        raise HTTPException(
            409, "A demo run is in progress — stop it before starting the simulator"
        )

    if req.device_id is not None:
        device = db.get_device(req.device_id)
        if device is None:
            raise HTTPException(404, f"Device '{req.device_id}' not found")
    else:
        devices = db.list_devices()
        if not devices:
            raise HTTPException(
                400, "No IoT device registered — register a device first"
            )
        device = devices[-1]

    plot = db.get_plot(device["plot_id"])
    location = (
        {"latitude": plot["latitude"], "longitude": plot["longitude"]}
        if plot else None
    )
    iot.start_simulation(
        device["id"], device["device_id"], location,
        scenario=req.scenario, interval=req.interval_seconds,
    )
    return {"simulation": iot.simulation_state()}


@app.get("/api/iot/simulation")
def get_simulation():
    """Current simulator state (running, scenario, interval, tick count)."""
    return {"simulation": iot.simulation_state()}


# ---------------- IoT → Farmer: alerts, Run Demo, export (Phase 3) ----------------


def _ensure_demo_device():
    """SOIL-001 bound to the demo farmer's first plot; idempotent.

    Mirrors tools/seed_demo.py identity (Abebe Getachew / North Ridge) so
    a fresh database can serve a full demo without seeding first.
    """
    device = db.get_device("SOIL-001")
    if device is not None:
        return device
    farmer = next(
        (f for f in db.list_farmer_summaries()
         if f["name"] == "Abebe Getachew"),
        None,
    )
    if farmer is None:
        farmer_id = db.create_farmer(
            "Abebe Getachew", phone="0911234567",
            region="Oromia-Arsi", lang="en",
        )
    else:
        farmer_id = farmer["id"]
    plots = db.list_farmer_plots(farmer_id)
    if plots:
        plot_id = plots[0]["id"]
    else:
        plot_id = db.create_plot(
            farmer_id, "North Ridge", farm_size_ha=2.1,
            latitude=7.95, longitude=39.13, previous_crop="maize",
        )
    db.register_device(
        "SOIL-001", "Oromia Soil Prototype 01", plot_id,
        "soil001-demo-token", battery=87.0, signal=-61.0,
    )
    return db.get_device("SOIL-001")


@app.get("/api/iot/alerts")
def list_iot_alerts(
    device_id: str | None = Query(None, max_length=40),
    status: Literal["active", "resolved", "all"] = "active",
    limit: int = Query(50, ge=1, le=200),
):
    """Advisory alerts newest-first, joined with device/plot/farmer context.

    `active_count` drives the header badge regardless of the page size.
    """
    device_row = None
    if device_id is not None:
        device = db.get_device(device_id)
        if device is None:
            raise HTTPException(404, f"Device '{device_id}' not found")
        device_row = device["id"]
    return {
        "alerts": db.list_alerts(
            device_row, status=None if status == "all" else status, limit=limit
        ),
        "active_count": db.count_active_alerts(device_row),
    }


@app.post("/api/iot/alerts/{alert_id}/ack")
def ack_iot_alert(alert_id: int):
    """Agent acknowledges an active alert (marks it resolved as 'acked')."""
    alert = db.ack_alert(alert_id)
    if alert is None:
        raise HTTPException(
            404, f"Alert {alert_id} not found or already resolved"
        )
    return {"alert": alert, "acknowledged": True,
            "active_count": db.count_active_alerts()}


@app.post("/api/iot/demo/run")
def run_demo(req: DemoRunIn, x_control_token: str | None = Header(None)):
    """One-click reproducible demo: ensure the demo device, backfill a
    believable reading history for the chosen scenario, and let the
    advisory engine surface any alerts — everything offline (ChatGPT §20).

    Single-writer rules (F4): the demo slot rejects concurrent demo runs
    (409), and a simulator bound to the demo device is stopped before the
    backfill starts (`simulation_stopped` in the response). IOT_CONTROL_TOKEN
    guards the endpoint when configured (F3).
    """
    _require_control_token(x_control_token)
    if not iot.acquire_demo_slot():
        raise HTTPException(409, "A demo run is already in progress")
    try:
        device = _ensure_demo_device()
        # One writer per device: halt an in-process simulator driving this
        # device before backfilling over it (a simulator on another device
        # keeps running — different writer, different device).
        simulation_stopped = False
        state = iot.simulation_state()
        if state.get("running") and state.get("device_id") == device["device_id"]:
            iot.stop_simulation()
            simulation_stopped = True
        created = iot.backfill_readings(
            device, scenario=req.scenario, count=req.count, reset=req.reset
        )
        device = db.get_device("SOIL-001")  # refresh heartbeat/view
        return {
            "device_id": device["device_id"],
            "scenario": req.scenario,
            "readings_created": created,
            "simulation_stopped": simulation_stopped,
            "active_alerts": db.count_active_alerts(device["id"]),
            "device": _device_view(device),
        }
    finally:
        iot.release_demo_slot()


@app.get("/api/iot/readings/export")
def export_readings(
    device_id: str = Query(..., min_length=1, max_length=40),
    limit: int = Query(500, ge=1, le=5000),
):
    """CSV download of a device's readings (chronological, spreadsheet-ready)."""
    device = db.get_device(device_id)
    if device is None:
        raise HTTPException(404, f"Device '{device_id}' not found")
    # list_readings is newest-first; flip to chronological for spreadsheets.
    rows = list(reversed(db.list_readings(device["id"], limit=limit)))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "reading_ts", "received_at", "source", "quality", "flags",
        "N_kg_ha", "P_kg_ha", "K_kg_ha", "ph", "moisture_pct", "temp_c",
        "latitude", "longitude",
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["reading_ts"], r["received_at"], r["source"],
            r["quality"], ";".join(r["flags"]),
            r["N"], r["P"], r["K"], r["ph"], r["moisture"], r["temperature"],
            r["latitude"], r["longitude"],
        ])
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="readings_{device["device_id"]}.csv"'
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
