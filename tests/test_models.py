import sys
import unittest
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import joblib
import pandas as pd

import config


class TestSoilModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.model = joblib.load(config.SOIL_MODEL_PKL)
            cls.df = pd.read_csv(config.SOIL_DATA_CSV)

    def test_predicts_three_classes(self):
        pred = self.model.predict([[50, 20, 120, 6.5]])[0]
        self.assertIn(pred, ["Good", "Moderate", "Poor"])

    def test_all_training_rows_predict(self):
        preds = self.model.predict(self.df[config.FEATURE_COLS].head(200))
        self.assertEqual(len(preds), 200)
        self.assertTrue(set(preds) <= {"Good", "Moderate", "Poor"})

    def test_no_missing_predictions(self):
        preds = self.model.predict(self.df[config.FEATURE_COLS].head(200))
        self.assertNotIn(None, preds.tolist())


class TestCropModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.model = joblib.load(config.CROP_MODEL_PKL)
            cls.df = pd.read_csv(config.CROP_DATA_CSV)

    def test_predicts_among_22_crops(self):
        row = self.df.iloc[0]
        pred = self.model.predict([[row[c] for c in config.CROP_FEATURES]])[0]
        self.assertIn(pred, set(self.df["label"]))
        self.assertEqual(len(self.model.classes_), 22)

    def test_probabilities_sum_to_one(self):
        row = self.df.iloc[0]
        probs = self.model.predict_proba([[row[c] for c in config.CROP_FEATURES]])[0]
        self.assertAlmostEqual(probs.sum(), 1.0)


if __name__ == "__main__":
    unittest.main()
