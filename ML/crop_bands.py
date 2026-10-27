"""Compute per-crop nutrient ideal bands from the real dataset.

For every crop in Crop_recommendation.csv we take a percentile band of N, P, K
and pH across that crop's 100 real rows. The percentile width is a policy
(band_policy): the default "legacy" is the original 40th-60th split, while
"middle_third" (33-67), "iqr" (25-75) and "recommended" (IQR plus P 35-65 /
pH 40-60 overrides) are opt-in. If a band collapses to a near-identical range
(e.g. a hard-coded field), we widen it around the median instead. Results are
saved to ML/crop_bands.json and are used by the engine for crop-specific
recommendations.

A coefficient-of-variation (CV) quality gate flags columns whose spread is
below band_policy.MIN_ALLOWED_CV_PCT (synthetic / hard-coded candidates); the
report is written to ML/crop_bands.quality.json.

Usage:
    python3 ML/crop_bands.py                       # legacy (unchanged output)
    python3 ML/crop_bands.py --policy recommended   # opt into the wider bands
    BAND_PERCENTILE_POLICY=recommended python3 ML/crop_bands.py
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import band_policy  # noqa: E402
import config  # noqa: E402

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


def compute_bands(df, policy=None):
    """{crop: {col: [lo, hi]}} plus the CV quality report, for a policy."""
    bands = {}
    quality = {}
    for crop in sorted(df["label"].unique()):
        sub = df.loc[df["label"] == crop, config.FEATURE_COLS]
        crop_bands = {}
        crop_quality = {}
        for col in config.FEATURE_COLS:
            lo_q, hi_q = band_policy.resolve(col, policy)
            lo, hi = sub[col].quantile([lo_q, hi_q])
            lo, hi = widen(lo, hi, sub[col].median(), col)
            crop_bands[col] = [float(round(lo, 2)), float(round(hi, 2))]
            crop_quality[col] = band_policy.data_quality(sub[col], col,
                                                         config.BAND_MIN_CV_PCT)
        bands[crop] = crop_bands
        quality[crop] = crop_quality
    return bands, quality


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy", default=None, choices=band_policy.policy_names(),
        help="Percentile-width policy (default: BAND_PERCENTILE_POLICY env "
             "or 'legacy').")
    parser.add_argument(
        "--out", type=Path, default=None,
        help=f"Output path (default: {config.CROP_BANDS_JSON}).")
    parser.add_argument(
        "--quality-report", type=Path, default=None,
        help="Write the CV quality report JSON here (default: "
             "ML/crop_bands.quality.json).")
    args = parser.parse_args()

    policy = args.policy or config.BAND_PERCENTILE_POLICY
    df = pd.read_csv(config.CROP_DATA_CSV)
    bands, quality = compute_bands(df, policy)

    out_path = args.out or Path(config.CROP_BANDS_JSON)
    with open(out_path, "w") as fh:
        json.dump(bands, fh, indent=2, sort_keys=True)

    report_path = args.quality_report or out_path.with_name(
        "crop_bands.quality.json")
    flagged = {
        crop: {col: q for col, q in cols.items() if q["flag"]}
        for crop, cols in quality.items()
    }
    flagged = {crop: cols for crop, cols in flagged.items() if cols}
    report = {
        "policy": policy,
        "policy_definition": band_policy.policy_definition(policy),
        "min_allowed_cv_pct": config.BAND_MIN_CV_PCT,
        "flagged_count": len(flagged),
        "flagged": flagged,
        "quality": quality,
    }
    with open(report_path, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    print(f"Saved bands for {len(bands)} crops to {out_path} (policy={policy})")
    if flagged:
        print(f"  CV gate flagged {len(flagged)} crop(s) as "
              f"synthetic/hard-coded: {', '.join(sorted(flagged))}")
    print(f"  quality report: {report_path}")
    for crop in sorted(bands)[:6]:
        print(f"  {crop:12s} N {bands[crop]['N']}  P {bands[crop]['P']}  "
              f"K {bands[crop]['K']}  ph {bands[crop]['ph']}")


if __name__ == "__main__":
    main()
