"""Generate a zone-synthetic Ethiopian soil dataset for fertility training.

The Kaggle soil CSV (Dataset/soil_data.csv) covers Indian ranges, so the
fertility model never saw Ethiopian soil values. This tool samples N/P/K/pH
realistically per agroecological zone (Dataset/agroecological_zones.json):
mass in the ideal band, thin near/excess tails, then labels each row with
THAT row's zone bands (same 2/1/0 scoring as data_analysis.py). The model
therefore learns an Ethiopian-context fertility function from values alone.

Usage:
    python3 tools/generate_zone_dataset.py [--rows 12000] [--seed 40]

Writes Dataset/soil_data_ethio.csv (columns N,P,K,ph,zone,Fertility_Status)
and Dataset/soil_data_ethio.meta.json (sha256, row counts per class/zone).
Reproducible for a given --seed. Train with:

    SOIL_TRAIN_DATA=Dataset/soil_data_ethio.csv \
    SOIL_MODEL_OUT=ML/soil_analysis_ethio.pkl \
    SOIL_MODEL_TAG=random-forest-v5-agroecology-zones \
    python3 ML/ml_model.py

v6 (soil_type for the Ethiopian LRF schema; leaves the v5 files untouched):

    python3 tools/generate_zone_dataset.py --soil-type \
        --out Dataset/soil_data_ethio_v6.csv

    SOIL_TRAIN_DATA=Dataset/soil_data_ethio_v6.csv \
    SOIL_MODEL_OUT=ML/soil_analysis_ethio_v6.pkl \
    SOIL_MODEL_TAG=random-forest-v6-soiltype-lrf \
    python3 ML/ml_model.py
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import ethiopian_standards as ethio  # noqa: E402
from data_analysis import GOOD_SCORE, MODERATE_SCORE, score_nutrient  # noqa: E402

# Share of rows drawn per zone (highland zones dominate Ethiopian cropland).
ZONE_WEIGHTS = {
    "highland_nitisols": 0.30,
    "highland_vertisols": 0.30,
    "rift_valley": 0.20,
    "arid_semi_arid": 0.20,
}

# Physical sampling bounds per column (clipped draws; never negative).
# K tops out at 1500: arid-zone near-bands reach 1300 kg/ha (K-rich soils).
COL_BOUNDS = {
    "N": (0.0, 400.0),
    "P": (0.0, 150.0),
    "K": (0.0, 1500.0),
    "ph": (3.5, 9.5),
}

# Probabilities: ideal core / near-low / near-high / deficient tail / excess tail.
_SEGMENTS = (
    ("ideal", 0.40),
    ("near_low", 0.15),
    ("near_high", 0.15),
    ("tail_low", 0.15),
    ("tail_high", 0.15),
)

# Per-zone soil-type mix for --soil-type (v6 dataset). Soil type varies
# WITHIN each zone (dominant type + minority pockets) so the one-hot
# soil_type features are not collinear with the zone columns and the model
# can actually learn from them. Labels still come from the zone bands.
ZONE_SOIL_MIX = {
    "highland_nitisols": {"nitisol": 0.75, "andosol": 0.15,
                          "cambisol_luvisol": 0.10},
    "highland_vertisols": {"vertisol": 0.80, "cambisol_luvisol": 0.20},
    "rift_valley": {"cambisol_luvisol": 0.40, "arenosol": 0.35,
                    "andosol": 0.25},
    "arid_semi_arid": {"arenosol": 0.50, "cambisol_luvisol": 0.50},
}

MIN_CLASS_ROWS = 100
MAX_ATTEMPTS = 8


def _clamp(value, lo, hi):
    return min(hi, max(lo, value))


def _draw_column(rng, ideal, near, lo, hi):
    """One value from the band-centered segment mixture for a column."""
    ideal_lo, ideal_hi = ideal
    near_lo, near_hi = near
    near_lo = _clamp(near_lo, lo, hi)
    near_hi = _clamp(near_hi, lo, hi)
    ideal_lo = _clamp(ideal_lo, lo, hi)
    ideal_hi = _clamp(ideal_hi, lo, hi)
    segments = []
    for name, prob in _SEGMENTS:
        if name == "ideal":
            segments.append((prob, ideal_lo, ideal_hi))
        elif name == "near_low" and ideal_lo > near_lo:
            segments.append((prob, near_lo, ideal_lo))
        elif name == "near_high" and ideal_hi < near_hi:
            segments.append((prob, ideal_hi, near_hi))
        elif name == "tail_low" and ideal_lo > near_lo:
            span = ideal_lo - near_lo
            segments.append((prob, _clamp(near_lo - 0.5 * span, lo, hi),
                             near_lo))
        elif name == "tail_high" and ideal_hi < near_hi:
            span = near_hi - ideal_hi
            segments.append((prob, near_hi,
                             _clamp(near_hi + 0.5 * span, lo, hi)))
    segments = [(p, a, b) for p, a, b in segments if b >= a]
    if not segments:
        segments.append((1.0, _clamp(near_lo, lo, hi), _clamp(near_hi, lo, hi)))
    total = sum(p for p, _, _ in segments)
    r = rng.random() * total
    for p, seg_lo, seg_hi in segments[:-1]:
        if r < p:
            return float(rng.uniform(seg_lo, seg_hi))
        r -= p
    _, seg_lo, seg_hi = segments[-1]
    return float(rng.uniform(seg_lo, seg_hi))


def _label_row(row, bands):
    score = 0
    for col, col_bands in bands.items():
        score += score_nutrient(
            row[col], *col_bands["ideal"], *col_bands["near"])
    if score >= GOOD_SCORE:
        return "Good"
    if score >= MODERATE_SCORE:
        return "Moderate"
    return "Poor"


def _draw_soil_type(rng, zone):
    """One soil-type id from the zone's mix (see ZONE_SOIL_MIX)."""
    mix = ZONE_SOIL_MIX.get(zone) or {}
    if not mix:
        return ethio.zone_soil_default(zone)
    ids = list(mix.keys())
    probs = np.array([mix[s] for s in ids], dtype=float)
    probs = probs / probs.sum()
    return str(rng.choice(ids, p=probs))


def generate(rows, seed, with_soil_type=False):
    rng = np.random.default_rng(seed)
    zones = ethio.list_zones()
    weights = np.array([ZONE_WEIGHTS[z] for z in zones])
    weights = weights / weights.sum()

    records = []
    for _ in range(rows):
        zone = str(rng.choice(zones, p=weights))
        bands = ethio.zone_labeling_bands(zone)
        rec = {"zone": zone}
        if with_soil_type:
            rec["soil_type"] = _draw_soil_type(rng, zone)
        for col, col_bands in bands.items():
            lo, hi = COL_BOUNDS[col]
            rec[col] = _draw_column(
                rng, col_bands["ideal"], col_bands["near"], lo, hi)
        rec[config.TARGET] = _label_row(rec, bands)
        records.append(rec)

    df = pd.DataFrame.from_records(records)
    columns = ["N", "P", "K", "ph", "zone"]
    if with_soil_type:
        columns.append("soil_type")
    df = df[columns + [config.TARGET]]
    for col in ("N", "P", "K", "ph"):
        df[col] = df[col].round(2)
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=40)
    parser.add_argument(
        "--soil-type", action="store_true",
        help="Add a within-zone soil_type column (v6 LRF schema). Keep the "
             "default output path consistent by pairing with --out.")
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output CSV path (default: Dataset/soil_data_ethio.csv; use "
             "Dataset/soil_data_ethio_v6.csv with --soil-type to leave the "
             "v5 artifact untouched).")
    args = parser.parse_args()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        df = generate(args.rows, args.seed + attempt - 1,
                      with_soil_type=args.soil_type)
        counts = df[config.TARGET].value_counts()
        if (counts.reindex(["Good", "Moderate", "Poor"]).fillna(0)
                >= MIN_CLASS_ROWS).all():
            break
        print(f"attempt {attempt}: unbalanced {counts.to_dict()}, retrying")
    else:
        raise SystemExit(
            f"could not reach >= {MIN_CLASS_ROWS} rows per class")

    out_csv = args.out or config.SOIL_DATA_ETHIO_CSV
    df.to_csv(out_csv, index=False)
    digest = hashlib.sha256(out_csv.read_bytes()).hexdigest()

    meta = {
        "rows": int(len(df)),
        "seed": int(args.seed),
        "dataset_sha256": digest,
        "class_counts": {k: int(v) for k, v in counts.items()},
        "zone_counts": {k: int(v) for k, v in df["zone"].value_counts().items()},
        "labeling": "per-row zone bands (Dataset/agroecological_zones.json)",
        "generator": "tools/generate_zone_dataset.py",
        # Provenance for anyone reading the dataset without the README:
        # the labels are a deterministic function of the sampled features,
        # so stratified CV measures rule reproduction, not field accuracy.
        "provenance": "synthetic",
        "label_origin": "rule_derived_from_zone_bands",
        "intended_use": "zone-consistent triage / rule distillation; not "
                         "evidence of Ethiopian field accuracy",
        "validation_note": "hold out a whole zone to measure transfer; "
                           "see docs/DATA_CARD.md",
    }
    if args.soil_type:
        meta["soil_type_mix"] = ZONE_SOIL_MIX
        meta["soil_type_counts"] = {
            k: int(v) for k, v in df["soil_type"].value_counts().items()}
        meta["schema"] = ["N", "P", "K", "ph", "zone", "soil_type",
                          config.TARGET]
    meta_path = out_csv.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"wrote {out_csv} ({len(df)} rows, sha256={digest[:12]}…)")
    print(f"  class counts: {meta['class_counts']}")
    print(f"  zone counts:  {meta['zone_counts']}")
    if args.soil_type:
        print(f"  soil types:   {meta['soil_type_counts']}")
    print(f"  meta:         {meta_path}")


if __name__ == "__main__":
    main()
