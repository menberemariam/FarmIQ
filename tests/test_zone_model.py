"""Tests for the zone-synthetic dataset and the dual soil-model artifacts.

Phase 2 contract:
  * Dataset/soil_data_ethio.csv exists, covers all four zones, and has every
    fertility class learnable (>=100 rows); its meta sha256 matches the file;
  * build_features() one-hot encodes the zone in canonical order, and the
    trained ethio artifact carries that schema in feature_cols_;
  * build_soil_row() aligns inference rows to either schema — the legacy
    4-column model keeps its exact input, the zone model gets zone dummies;
  * config.soil_model_path() selects the artifact from SOIL_MODEL_PROFILE;
  * importing ML.ml_model does not trigger training (main-guard).
"""

import hashlib
import json
import sys
import unittest
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import joblib
import pandas as pd

import config
import ethiopian_standards as ethio
from api import build_soil_row
from ML.ml_model import build_features, dataset_sha256, default_tag


def _ethio_dataset():
    return pd.read_csv(config.SOIL_DATA_ETHIO_CSV)


class TestZoneDataset(unittest.TestCase):
    def test_dataset_and_meta_exist(self):
        self.assertTrue(config.SOIL_DATA_ETHIO_CSV.exists())
        meta_path = config.SOIL_DATA_ETHIO_CSV.with_suffix(".meta.json")
        self.assertTrue(meta_path.exists())

    def test_meta_sha256_matches_file(self):
        meta = json.loads(
            config.SOIL_DATA_ETHIO_CSV.with_suffix(".meta.json").read_text())
        self.assertEqual(meta["dataset_sha256"],
                         dataset_sha256(config.SOIL_DATA_ETHIO_CSV))

    def test_all_zones_and_classes_present(self):
        df = _ethio_dataset()
        self.assertEqual(set(df["zone"]), set(ethio.list_zones()))
        counts = df[config.TARGET].value_counts()
        for cls in ("Good", "Moderate", "Poor"):
            self.assertGreaterEqual(int(counts.get(cls, 0)), 100, cls)

    def test_schema_matches_legacy_plus_zone(self):
        df = _ethio_dataset()
        self.assertEqual(list(df.columns),
                         ["N", "P", "K", "ph", "zone", config.TARGET])

    def test_values_stay_physical(self):
        df = _ethio_dataset()
        self.assertTrue((df[["N", "P", "K"]] >= 0).all().all())
        self.assertTrue(df["ph"].between(3.5, 9.5).all())


class TestBuildFeatures(unittest.TestCase):
    def test_zone_dummies_in_canonical_order(self):
        df = _ethio_dataset().head(20)
        X = build_features(df)
        expected = config.FEATURE_COLS + [f"zone_{z}" for z in
                                          ethio.list_zones()]
        self.assertEqual(list(X.columns), expected)
        self.assertTrue((X[expected[4:]].sum(axis=1) == 1).all())

    def test_zoneless_frame_keeps_legacy_schema(self):
        df = pd.DataFrame({"N": [50], "P": [20], "K": [120], "ph": [6.5],
                           config.TARGET: ["Good"]})
        X = build_features(df)
        self.assertEqual(list(X.columns), config.FEATURE_COLS)

    def test_ethio_artifact_exposes_schema(self):
        if not config.SOIL_MODEL_ETHIO_PKL.exists():
            self.skipTest("ethio model artifact not trained")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = joblib.load(config.SOIL_MODEL_ETHIO_PKL)
        self.assertEqual(
            list(model.feature_cols_),
            config.FEATURE_COLS + [f"zone_{z}" for z in ethio.list_zones()])


class TestBuildSoilRow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.legacy = joblib.load(config.SOIL_MODEL_PKL)
        cls.row = pd.DataFrame(
            [{"N": 150, "P": 40, "K": 300, "ph": 5.2,
              "zone": "highland_nitisols"}])

    def test_legacy_model_row_is_exactly_four_columns(self):
        row = build_soil_row(self.row, self.legacy)
        self.assertEqual(list(row.columns), config.FEATURE_COLS)

    def test_zone_model_row_gets_one_dummy(self):
        if not config.SOIL_MODEL_ETHIO_PKL.exists():
            self.skipTest("ethio model artifact not trained")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = joblib.load(config.SOIL_MODEL_ETHIO_PKL)
        row = build_soil_row(self.row, model)
        self.assertEqual(list(row.columns), list(model.feature_cols_))
        self.assertEqual(int(row["zone_highland_nitisols"].iloc[0]), 1)
        self.assertEqual(int(row["zone_arid_semi_arid"].iloc[0]), 0)

    def test_zone_model_row_without_zone_is_all_zero_dummies(self):
        if not config.SOIL_MODEL_ETHIO_PKL.exists():
            self.skipTest("ethio model artifact not trained")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = joblib.load(config.SOIL_MODEL_ETHIO_PKL)
        nozone = self.row.drop(columns=["zone"])
        row = build_soil_row(nozone, model)
        zone_cols = [c for c in model.feature_cols_ if c.startswith("zone_")]
        self.assertTrue((row[zone_cols] == 0).all().all())
        self.assertIn(model.predict(row)[0], ("Good", "Moderate", "Poor"))

    def test_zone_change_shifts_zone_model_verdict(self):
        """The whole point of the zone feature: same soil, different zone,
        different fertility verdict."""
        if not config.SOIL_MODEL_ETHIO_PKL.exists():
            self.skipTest("ethio model artifact not trained")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = joblib.load(config.SOIL_MODEL_ETHIO_PKL)
        preds = []
        for zone in ("highland_nitisols", "arid_semi_arid"):
            row = self.row.copy()
            row["zone"] = zone
            preds.append(model.predict(build_soil_row(row, model))[0])
        self.assertNotEqual(preds[0], preds[1],
                            f"zone-blind verdicts: {preds}")


class TestProfileSelection(unittest.TestCase):
    def setUp(self):
        self._prev = config.SOIL_MODEL_PROFILE

    def tearDown(self):
        config.SOIL_MODEL_PROFILE = self._prev

    def test_default_profile_selects_legacy_artifact(self):
        config.SOIL_MODEL_PROFILE = ""
        self.assertEqual(config.soil_model_path(), config.SOIL_MODEL_PKL)

    def test_agroecology_profile_selects_ethio_artifact(self):
        for value in ("agroecology", "ethio"):
            config.SOIL_MODEL_PROFILE = value
            self.assertEqual(config.soil_model_path(),
                             config.SOIL_MODEL_ETHIO_PKL)


class TestMlModelModule(unittest.TestCase):
    def test_import_does_not_train(self):
        # ml_model.main() is main-guarded; importing must be side-effect free
        # (regression: the old module trained at import time).
        import ML.ml_model as ml_model
        self.assertTrue(callable(ml_model.main))
        self.assertTrue(callable(ml_model.build_features))

    def test_default_tag_matches_output_name(self):
        self.assertEqual(default_tag(Path("ML/soil_analysis.pkl")),
                         "random-forest-v4-ethiosis-p2.6")
        self.assertEqual(default_tag(Path("ML/soil_analysis_ethio.pkl")),
                         "random-forest-v5-agroecology-zones")


if __name__ == "__main__":
    unittest.main()
