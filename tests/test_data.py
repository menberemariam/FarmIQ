import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import data_analysis


class TestPaths(unittest.TestCase):
    def test_datasets_exist(self):
        self.assertTrue(config.SOIL_DATA_CSV.exists())
        self.assertTrue(config.CROP_DATA_CSV.exists())

    def test_models_exist(self):
        self.assertTrue(config.SOIL_MODEL_PKL.exists())
        self.assertTrue(config.CROP_MODEL_PKL.exists())

    def test_no_literal_backslash_files(self):
        for candidate in PROJECT_ROOT.glob("*\\*"):
            self.fail(f"Literal-backslash filename found: {candidate}")


class TestScoring(unittest.TestCase):
    def test_no_dead_zones_in_data_range(self):
        """Bands must be contiguous within each nutrient: everything from the near-lo
        edge through the near-hi edge scores >= 1, and the ideal band scores 2.
        No value inside the covered span may fall into an unscored gap."""
        for col, bands in data_analysis.IDEAL_BANDS.items():
            near_lo, near_hi = bands["near"]
            ideal_lo, ideal_hi = bands["ideal"]
            self.assertLessEqual(near_lo, ideal_lo, f"{col}: near band must span ideal")
            self.assertLessEqual(ideal_hi, near_hi, f"{col}: ideal must fit inside near")
            for value in (near_lo, ideal_lo - 0.01, ideal_lo, ideal_hi, ideal_hi + 0.01, near_hi):
                score = data_analysis.score_nutrient(value, *bands["ideal"], *bands["near"])
                self.assertGreaterEqual(score, 1, f"{col}: value {value} falls in a dead zone")
            self.assertEqual(
                data_analysis.score_nutrient(ideal_lo, *bands["ideal"], *bands["near"]), 2
            )

    def test_high_p_not_rewarded(self):
        """Regression: P > 80 must not score 2 points (the old labeling bug that made
        P-rich soils 'Good')."""
        self.assertEqual(data_analysis.score_nutrient(100, 15, 30, 10, 45), 0)
        self.assertEqual(data_analysis.score_nutrient(145, 15, 30, 10, 45), 0)

    def test_ideal_band_scores_2(self):
        self.assertEqual(data_analysis.score_nutrient(50, 40, 60, 30, 75), 2)
        self.assertEqual(data_analysis.score_nutrient(6.5, 6, 7, 5.5, 8), 2)

    def test_label_distribution_is_learnable(self):
        import pandas as pd

        df = pd.read_csv(config.SOIL_DATA_CSV)
        counts = df[config.TARGET].value_counts()
        for cls in ("Good", "Moderate", "Poor"):
            self.assertGreaterEqual(counts.get(cls, 0), 100, f"{cls} too rare to learn")

    def test_good_soil_means_are_sane(self):
        """Good soil must no longer have a mean P of ~100 kg/ha."""
        import pandas as pd

        df = pd.read_csv(config.SOIL_DATA_CSV)
        good = df.loc[df[config.TARGET] == "Good", config.FEATURE_COLS].mean()
        self.assertLess(good["P"], 60)
        self.assertLess(good["K"], 120)


if __name__ == "__main__":
    unittest.main()
