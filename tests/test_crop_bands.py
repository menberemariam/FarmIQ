import sys
import unittest
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

import config
from ML.engine import analyze_soil, available_crops, get_bands


class TestCropBands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = pd.read_csv(config.CROP_DATA_CSV)

    def test_all_22_crops_have_real_bands(self):
        crops = available_crops()
        self.assertEqual(len(crops), 22)
        for crop in crops:
            bands = get_bands(crop)
            for col in config.FEATURE_COLS:
                lo, hi = bands[col]
            self.assertLess(lo, hi, f"{crop} {col}: degenerate band")
            data_lo = self.df.loc[self.df["label"] == crop, col].min()
            data_hi = self.df.loc[self.df["label"] == crop, col].max()
            center = (lo + hi) / 2
            # The band must be anchored in the real data: its center inside the
            # data range, and it must intersect the data range (widened
            # degenerate columns may extend slightly beyond min/max).
            self.assertGreaterEqual(center, data_lo, f"{crop} {col}: center below data min")
            self.assertLessEqual(center, data_hi, f"{crop} {col}: center above data max")
            self.assertLessEqual(lo, data_hi, f"{crop} {col}: band misses data range")
            self.assertGreaterEqual(hi, data_lo, f"{crop} {col}: band misses data range")

    def test_crop_bands_differ_from_generic(self):
        """Real crop bands must not be identical to the generic agronomic ranges."""
        for crop in ("rice", "apple", "banana"):
            self.assertNotEqual(get_bands(crop), get_bands(None), f"{crop} bands == generic")

    def test_unknown_crop_falls_back_to_generic(self):
        self.assertEqual(get_bands("nonexistent-crop"), config.IDEAL_RANGES)

    def test_bands_json_is_generated_from_dataset(self):
        import json

        with open(config.CROP_BANDS_JSON) as fh:
            bands = json.load(fh)
        self.assertEqual(len(bands), self.df["label"].nunique())


class TestCropAwareEngine(unittest.TestCase):
    def test_status_uses_crop_band(self):
        """N=55 is adequate for generic bands (40-60) but deficient for rice,
        whose real N band sits around 60-90 in the dataset."""
        generic = analyze_soil(55, 20, 110, 6.5, crop=None)
        rice = analyze_soil(55, 20, 110, 6.5, crop="rice")
        by_col_g = {a["col"]: a for a in generic}
        by_col_r = {a["col"]: a for a in rice}
        self.assertEqual(by_col_g["N"]["status"], "adequate")
        self.assertEqual(by_col_r["N"]["status"], "deficient")
        self.assertIn("band", by_col_r["N"])
        self.assertAlmostEqual(
            by_col_r["N"]["deficit"], by_col_r["N"]["band"][0] - 55
        )

    def test_recommendation_mentions_crop_and_band(self):
        from ML.engine import recommend_soil

        recs = recommend_soil(analyze_soil(55, 20, 110, 6.5, crop="rice"))
        n_rec = next(r for r in recs if r["element"] == "Nitrogen")
        self.assertIn("rice", n_rec["solution"])
        self.assertGreater(len(n_rec["solution"]), 50)

    def test_prompt_contains_crop_bands(self):
        from ML.engine import build_prompt

        prompt = build_prompt(analyze_soil(55, 20, 110, 6.5, crop="rice"))
        self.assertIn("rice", prompt)
        self.assertIn("ideal band", prompt)


if __name__ == "__main__":
    unittest.main()
