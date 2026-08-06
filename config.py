from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DATASET_DIR = BASE_DIR / "Dataset"
ML_DIR = BASE_DIR / "ML"

SOIL_DATA_CSV = DATASET_DIR / "soil_data.csv"
CROP_DATA_CSV = DATASET_DIR / "Crop_recommendation.csv"

SOIL_MODEL_PKL = ML_DIR / "soil_analysis.pkl"
CROP_MODEL_PKL = ML_DIR / "crop_recommendation.pkl"
CROP_BANDS_JSON = ML_DIR / "crop_bands.json"

DB_PATH = BASE_DIR / "soil_reports.db"

FEATURE_COLS = ["N", "P", "K", "ph"]
TARGET = "Fertility_Status"

IDEAL_RANGES = {
    "N": (40.0, 60.0),
    "P": (15.0, 30.0),
    "K": (100.0, 150.0),
    "ph": (6.0, 7.0),
}

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

CROP_FEATURES = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
