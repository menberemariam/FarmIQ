"""Enhancement B — configurable percentile bands + CV quality gate.

Pins the additive contract:
  * the default policy ("legacy") resolves to the original 40th-60th split
    and regenerates ML/crop_bands.json byte-for-byte unchanged;
  * "middle_third" (33-67), "iqr" (25-75) and "recommended" (IQR plus
    P 35-65 / pH 40-60) resolve per-column as specified;
  * percentile_band / coefficient_of_variation / data_quality behave and the
    CV gate flags implausibly tight (synthetic/hard-coded) columns;
  * switching policy only widens the lookup bands — every consumer stays
    anchored in the real data range.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

import band_policy  # noqa: E402
import config  # noqa: E402
from ML.crop_bands import compute_bands  # noqa: E402

LEGACY = (0.40, 0.60)
MIDDLE_THIRD = (0.33, 0.67)
IQR = (0.25, 0.75)


class TestPolicyResolution(unittest.TestCase):
    def test_legacy_is_the_default_and_original_split(self):
        self.assertEqual(band_policy.DEFAULT_POLICY, "legacy")
        self.assertEqual(config.BAND_PERCENTILE_POLICY, "legacy")
        for col in band_policy.COLS:
            self.assertEqual(band_policy.resolve(col, "legacy"), LEGACY)

    def test_middle_third_and_iqr_apply_to_every_column(self):
        for col in band_policy.COLS:
            self.assertEqual(band_policy.resolve(col, "middle_third"),
                             MIDDLE_THIRD)
            self.assertEqual(band_policy.resolve(col, "iqr"), IQR)

    def test_recommended_per_column_overrides(self):
        self.assertEqual(band_policy.resolve("N", "recommended"), IQR)
        self.assertEqual(band_policy.resolve("K", "recommended"), IQR)
        self.assertEqual(band_policy.resolve("P", "recommended"), (0.35, 0.65))
        self.assertEqual(band_policy.resolve("ph", "recommended"), LEGACY)

    def test_unknown_policy_or_column_falls_back(self):
        self.assertEqual(band_policy.resolve("N", "nonsense"), LEGACY)
        self.assertEqual(band_policy.resolve("zzz", "iqr"), IQR)

    def test_policy_definition_matches_production_block(self):
        block = band_policy.policy_definition("recommended")
        self.assertEqual(block["default_statistical_bounds"][
            "stable_middle_third"], [33, 67])
        self.assertEqual(block["default_statistical_bounds"][
            "standard_iqr_optimal"], [25, 75])
        self.assertEqual(block["resolved_percentiles"]["P"], [0.35, 0.65])
        self.assertEqual(block["resolved_percentiles"]["ph"], [0.4, 0.6])
        self.assertEqual(block["data_quality_gates"][
            "minimum_allowed_cv_percentage"], 15.0)
        self.assertEqual(block["data_quality_gates"]["action_on_fail"],
                         "FLAG_AS_HARDCODED_OR_SYNTHETIC")


class TestStatistics(unittest.TestCase):
    def test_percentile_band(self):
        values = list(range(1, 101))  # 1..100
        lo, hi = band_policy.percentile_band(values, "N", "legacy")
        self.assertAlmostEqual(lo, 40.6)
        self.assertAlmostEqual(hi, 60.4)
        iqr_lo, iqr_hi = band_policy.percentile_band(values, "N", "iqr")
        self.assertLess(iqr_lo, lo)
        self.assertGreater(iqr_hi, hi)

    def test_coefficient_of_variation(self):
        self.assertEqual(band_policy.coefficient_of_variation([5, 5, 5, 5]), 0.0)
        self.assertEqual(band_policy.coefficient_of_variation([]), 0.0)
        self.assertAlmostEqual(
            band_policy.coefficient_of_variation([10, 20, 30]), 50.0, places=6)

    def test_data_quality_flags_tight_and_passes_wide(self):
        tight = band_policy.data_quality([100.0, 100.5, 100.2, 100.1])
        self.assertTrue(tight["flag"])
        self.assertEqual(tight["action"], "FLAG_AS_HARDCODED_OR_SYNTHETIC")
        self.assertLess(tight["cv_pct"], 15.0)

        wide = band_policy.data_quality([10, 40, 70, 100, 130])
        self.assertFalse(wide["flag"])
        self.assertIsNone(wide["action"])

    def test_gate_threshold_override(self):
        values = [100.0, 105.0, 110.0, 95.0]  # CV well below 15
        self.assertTrue(band_policy.data_quality(values)["flag"])
        self.assertFalse(band_policy.data_quality(values, min_cv_pct=1.0)["flag"])


class TestGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = pd.read_csv(config.CROP_DATA_CSV)

    def test_legacy_regenerates_shipped_artifact_byte_for_byte(self):
        bands, _ = compute_bands(self.df, "legacy")
        with open(config.CROP_BANDS_JSON) as fh:
            shipped = json.load(fh)
        self.assertEqual(bands, shipped)

    def test_default_policy_matches_config(self):
        config_bands, _ = compute_bands(self.df, config.BAND_PERCENTILE_POLICY)
        legacy_bands, _ = compute_bands(self.df, "legacy")
        self.assertEqual(config_bands, legacy_bands)

    def test_recommended_bands_are_wider_and_anchored(self):
        legacy, _ = compute_bands(self.df, "legacy")
        rec, _ = compute_bands(self.df, "recommended")
        for col in config.FEATURE_COLS:
            l_lo, l_hi = legacy["rice"][col]
            r_lo, r_hi = rec["rice"][col]
            self.assertLessEqual(r_lo, l_lo + 1e-9, col)
            self.assertGreaterEqual(r_hi, l_hi - 1e-9, col)
            data_lo = self.df.loc[self.df["label"] == "rice", col].min()
            data_hi = self.df.loc[self.df["label"] == "rice", col].max()
            self.assertGreaterEqual((r_lo + r_hi) / 2, data_lo, col)
            self.assertLessEqual((r_lo + r_hi) / 2, data_hi, col)

    def test_recommended_ph_equals_legacy(self):
        legacy, _ = compute_bands(self.df, "legacy")
        rec, _ = compute_bands(self.df, "recommended")
        self.assertEqual(rec["rice"]["ph"], legacy["rice"]["ph"])

    def test_quality_report_flags_the_tight_k_column(self):
        _, quality = compute_bands(self.df, "legacy")
        # apple K is essentially constant in the dataset (CV ~1.7%).
        self.assertTrue(quality["apple"]["K"]["flag"])
        # maize N is a genuinely variable column (CV ~15.4%).
        self.assertFalse(quality["maize"]["N"]["flag"])


if __name__ == "__main__":
    unittest.main()
