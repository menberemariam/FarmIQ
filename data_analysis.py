import pandas as pd

import config

IDEAL_BANDS = {
    "N": {"ideal": (40.0, 60.0), "near": (30.0, 75.0)},
    "P": {"ideal": (15.0, 30.0), "near": (10.0, 45.0)},
    "K": {"ideal": (60.0, 150.0), "near": (40.0, 175.0)},
    "ph": {"ideal": (6.0, 7.0), "near": (5.5, 8.0)},
}

# "Good" requires score >= 5/8: e.g. two nutrients in the ideal band plus
# another near it, or one ideal plus all three near. "Moderate" >= 3/8.
# Note: K's ideal band is dataset-aware (60-150) because this Kaggle dataset
# is K-poor; config.IDEAL_RANGES keeps the agronomic 100-150 for advice.
GOOD_SCORE = 5
MODERATE_SCORE = 3


def score_nutrient(value, ideal_lo, ideal_hi, near_lo, near_hi):
    """2 points inside the ideal band, 1 point near it, 0 otherwise. Contiguous, no dead zones."""
    if ideal_lo <= value <= ideal_hi:
        return 2
    if near_lo <= value <= near_hi:
        return 1
    return 0


def fertility_status(row):
    score = 0
    for col, bands in IDEAL_BANDS.items():
        score += score_nutrient(row[col], *bands["ideal"], *bands["near"])
    if score >= GOOD_SCORE:
        return "Good"
    if score >= MODERATE_SCORE:
        return "Moderate"
    return "Poor"


def main():
    df = pd.read_csv(config.CROP_DATA_CSV)
    df = df[config.FEATURE_COLS].copy()
    df[config.TARGET] = df.apply(fertility_status, axis=1)
    counts = df[config.TARGET].value_counts()
    print(counts)
    df.to_csv(config.SOIL_DATA_CSV, index=False)
    good_means = df.loc[df[config.TARGET] == "Good", config.FEATURE_COLS].mean()
    print("Good-soil means:\n", good_means)


if __name__ == "__main__":
    main()
