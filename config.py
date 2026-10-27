import os
from pathlib import Path

import band_policy
import ethiopian_standards as ethio

BASE_DIR = Path(__file__).resolve().parent

DATASET_DIR = BASE_DIR / "Dataset"
ML_DIR = BASE_DIR / "ML"

SOIL_DATA_CSV = DATASET_DIR / "soil_data.csv"
CROP_DATA_CSV = DATASET_DIR / "Crop_recommendation.csv"
# Zone-synthetic Ethiopian training set (tools/generate_zone_dataset.py).
SOIL_DATA_ETHIO_CSV = DATASET_DIR / "soil_data_ethio.csv"
# v6 set: adds a within-zone soil_type column for the LRF-aware schema
# (generated with --soil-type; the v5 file stays untouched).
SOIL_DATA_ETHIO_V6_CSV = DATASET_DIR / "soil_data_ethio_v6.csv"

SOIL_MODEL_PKL = ML_DIR / "soil_analysis.pkl"
SOIL_MODEL_ETHIO_PKL = ML_DIR / "soil_analysis_ethio.pkl"
SOIL_MODEL_ETHIO_V6_PKL = ML_DIR / "soil_analysis_ethio_v6.pkl"
SOIL_MODEL_ETHIO_V7_PKL = ML_DIR / "soil_analysis_ethio_v7.pkl"
CROP_MODEL_PKL = ML_DIR / "crop_recommendation.pkl"
CROP_BANDS_JSON = ML_DIR / "crop_bands.json"
SEASONAL_BANDS_JSON = DATASET_DIR / "seasonal_bands.json"

# SOIL_DB_PATH isolates probes/tests from the live demo DB; default unchanged.
DB_PATH = Path(os.getenv("SOIL_DB_PATH", str(BASE_DIR / "soil_reports.db")))

FEATURE_COLS = ["N", "P", "K", "ph"]
TARGET = "Fertility_Status"

# Active fertility profile: "ethiosis" (EthioSIS/ESRI lab standards, default)
# or "generic" (legacy pre-EthioSIS bands). Set FERTILITY_PROFILE to override.
FERTILITY_PROFILE = ethio.get_profile()

# Which soil-model artifact the API serves:
#   "" / default     -> ML/soil_analysis.pkl (EthioSIS-labeled Kaggle data)
#   agroecology      -> ML/soil_analysis_ethio.pkl (zone-synthetic, v5)
#   agroecology_v6   -> ML/soil_analysis_ethio_v6.pkl (zone + soil_type, v6)
VALID_SOIL_MODEL_PROFILES = ("", "default", "agroecology", "ethio",
                             "agroecology_v6", "agroecology_v7")
SOIL_MODEL_PROFILE = os.getenv("SOIL_MODEL_PROFILE", "").strip().lower()
if SOIL_MODEL_PROFILE not in VALID_SOIL_MODEL_PROFILES:
    SOIL_MODEL_PROFILE = ""


def soil_model_path():
    """Path of the soil-fertility model pickle selected by SOIL_MODEL_PROFILE."""
    if SOIL_MODEL_PROFILE == "agroecology_v7":
        return SOIL_MODEL_ETHIO_V7_PKL
    if SOIL_MODEL_PROFILE == "agroecology_v6":
        return SOIL_MODEL_ETHIO_V6_PKL
    if SOIL_MODEL_PROFILE in ("agroecology", "ethio"):
        return SOIL_MODEL_ETHIO_PKL
    return SOIL_MODEL_PKL

# Generic advice ranges (model kg/ha units for N/P/K), profile-selected.
# ethiosis: EthioSIS good cores — N 0.15-0.25% (x400), P 30-45 mg/kg,
# K 0.2-0.4 cmolc/kg (x391), pH 6.5-7.5.
IDEAL_RANGES = ethio.advice_ranges(FERTILITY_PROFILE)

ELEMENTS = ["Nitrogen", "Phosphorus", "Potassium", "pH"]

ELEMENT_TO_COL = {
    "Nitrogen": "N",
    "Phosphorus": "P",
    "Potassium": "K",
    "pH": "ph",
}

UI_INPUT_RANGES = {
    "N": (0, 140),
    "P": (5, 145),
    "K": (5, 205),
    "ph": (3.5, 9.94),
}


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "off", "no", "")


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Opt-in capture of consented, unlabeled samples for future Ethiopian
# retraining. Off by default: the API and stored report schema are unchanged
# unless a client explicitly agrees and the deployment enables capture.
TRAINING_DATA_CAPTURE = _env_bool("TRAINING_DATA_CAPTURE", False)
TRAINING_REAL_CSV = DATASET_DIR / os.getenv(
    "TRAINING_REAL_CSV", "soil_data_ethio_real_v1.csv")
TRAINING_REAL_META = DATASET_DIR / os.getenv(
    "TRAINING_REAL_META", "soil_data_ethio_real_v1.meta.json")
TRAINING_V7_CSV = DATASET_DIR / os.getenv(
    "TRAINING_V7_CSV", "soil_data_ethio_v7.csv")
TRAINING_MIN_ROWS_PER_CLASS = _env_int("TRAINING_MIN_ROWS_PER_CLASS", 30)
TRAINING_MIN_ROWS_PER_ZONE = _env_int("TRAINING_MIN_ROWS_PER_ZONE", 10)


def training_capture_enabled():
    """Read the capture flag at call time so tests can monkeypatch config."""
    return TRAINING_DATA_CAPTURE


# LLM response cache (Enhancement 1). Transparent: the prompt hash includes
# every context field, so a hit is exactly the same request. Default on;
# tests clear the singleton or disable it explicitly.
LLM_CACHE_ENABLED = _env_bool("LLM_CACHE_ENABLED", True)
LLM_CACHE_MAXSIZE = _env_int("LLM_CACHE_MAXSIZE", 128)
LLM_CACHE_TTL_SECONDS = _env_float("LLM_CACHE_TTL_SECONDS", 86400.0)

# Additive contract counters on /api/health. Off by default so the payload is
# byte-identical for existing clients.
LLM_TELEMETRY = _env_bool("LLM_TELEMETRY", False)

CROP_FEATURES = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]

# Percentile-width policy for the per-crop lookup bands (Enhancement B).
# "legacy" (default) reproduces the original 40th-60th percentiles byte for
# byte; "middle_third" (33-67), "iqr" (25-75) and "recommended" (IQR plus
# P 35-65 / pH 40-60 overrides) are opt-in via BAND_PERCENTILE_POLICY.
BAND_PERCENTILE_POLICY = os.getenv("BAND_PERCENTILE_POLICY", "").strip().lower()
if not band_policy.is_valid_policy(BAND_PERCENTILE_POLICY):
    BAND_PERCENTILE_POLICY = band_policy.DEFAULT_POLICY
BAND_MIN_CV_PCT = band_policy.MIN_ALLOWED_CV_PCT
