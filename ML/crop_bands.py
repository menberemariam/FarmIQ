"""Compute per-crop nutrient ideal bands from the real dataset.

For every crop in Crop_recommendation.csv we take the 40th-60th percentile of
N, P, K and pH across that crop's 100 real rows. If the band collapses
(identical or near-identical percentiles, e.g. a hard-coded field), we widen it
around the median instead. Results are saved to ML/crop_bands.json and are used
by the engine for crop-specific recommendations.
"""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

PH_MIN_WIDTH = 0.3
NUTRIENT_MIN_FRACTION = 0.1


def widen(lo, hi, center, col):
    """Widen a degenerate band around the median so it is a usable range."""
    width = hi - lo
    if col == "ph":
        target = PH_MIN_WIDTH
    else:
        target = NUTRIENT_MIN_FRACTION * max(abs(center), 1.0)
    if width >= target:
        return lo, hi
    return center - target / 2, center + target / 2


def main():
    df = pd.read_csv(config.CROP_DATA_CSV)
    bands = {}
    for crop in sorted(df["label"].unique()):
        sub = df.loc[df["label"] == crop, config.FEATURE_COLS]
        crop_bands = {}
        for col in config.FEATURE_COLS:
            lo, hi = sub[col].quantile([0.4, 0.6])
            lo, hi = widen(lo, hi, sub[col].median(), col)
            crop_bands[col] = [round(lo, 2), round(hi, 2)]
        bands[crop] = crop_bands

    with open(config.CROP_BANDS_JSON, "w") as fh:
        json.dump(bands, fh, indent=2, sort_keys=True)

    print(f"Saved bands for {len(bands)} crops to {config.CROP_BANDS_JSON}")
    for crop in sorted(bands)[:6]:
        print(f"  {crop:12s} N {bands[crop]['N']}  P {bands[crop]['P']}  "
              f"K {bands[crop]['K']}  ph {bands[crop]['ph']}")


if __name__ == "__main__":
    main()
