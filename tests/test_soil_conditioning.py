"""Enhancement E: SOC/texture-conditioned advisory bands."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

_tmpdir = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmpdir.name) / "test_soil_conditioning.db"

import api  # noqa: E402
import db  # noqa: E402
import fertilizer_cost  # noqa: E402
import soil_conditioning  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import analyze_soil, get_bands, recommend_soil  # noqa: E402

client = TestClient(api.app)


class TestConditioningData(unittest.TestCase):
    def test_dataset_is_reference_only(self):
        meta = soil_conditioning.meta()
        self.assertEqual(meta["review_status"], "unverified")
        self.assertIn("NOT measured soil observations", meta["description"])
        self.assertEqual(meta["default_mode"], "explicit")

    def test_classes_and_aliases(self):
        self.assertEqual(
            [entry["id"] for entry in soil_conditioning.list_texture_classes()],
            ["clay", "loam", "sandy"],
        )
        self.assertEqual(
            [entry["id"] for entry in soil_conditioning.list_soc_classes()],
            ["very_low", "low", "medium", "high"],
        )
        self.assertEqual(soil_conditioning.canonical_texture("heavy clay"), "clay")
        self.assertEqual(soil_conditioning.canonical_texture("Nitisols"), "clay")
        self.assertEqual(soil_conditioning.canonical_soc_class("Very Low"), "very_low")
        self.assertIsNone(soil_conditioning.canonical_texture("gravel"))

    def test_soc_pct_maps_to_class(self):
        self.assertEqual(soil_conditioning.soc_class_for_pct(0.5), "very_low")
        self.assertEqual(soil_conditioning.soc_class_for_pct(1.5), "low")
        self.assertEqual(soil_conditioning.soc_class_for_pct(2.5), "medium")
        self.assertEqual(soil_conditioning.soc_class_for_pct(4.0), "high")


class TestConditioningEngine(unittest.TestCase):
    def test_no_inputs_is_legacy_noop(self):
        base = get_bands("maize")
        self.assertEqual(get_bands("maize", texture_class=None, soc_pct=None), base)
        legacy = analyze_soil(25, 10, 40, 5.5, crop="maize")
        conditioned = analyze_soil(
            25, 10, 40, 5.5, crop="maize",
            texture_class=None, soc_pct=None, soc_class=None, woreda=None,
        )
        self.assertEqual(legacy, conditioned)
        self.assertTrue(all("soil_conditioning" not in item for item in legacy))

    def test_soil_type_alone_does_not_activate(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOIL_CONDITIONING_ENABLED", None)
            os.environ.pop("SOIL_CONDITIONING_USE_SOIL_TYPE", None)
            self.assertEqual(
                get_bands("maize", soil_type="vertisol"), get_bands("maize"))

    def test_explicit_soc_and_texture_adjust_bands(self):
        bands = get_bands(
            "maize", texture_class="clay", soc_pct=0.5, ph=5.0)
        base = get_bands("maize")
        self.assertEqual(bands["N"][0], base["N"][0] + 20)
        self.assertEqual(bands["K"][0], base["K"][0] + 40)
        self.assertEqual(bands["K"][1], base["K"][1] + 50)
        self.assertEqual(bands["P"][0], base["P"][0] + 20)
        self.assertEqual(bands["P"][1], base["P"][1] + 30)

    def test_p_rule_only_below_ph_threshold(self):
        base = get_bands("maize")
        at_threshold = get_bands("maize", texture_class="clay", ph=5.5)
        below = get_bands("maize", texture_class="clay", ph=5.4)
        self.assertEqual(at_threshold["P"], base["P"])
        self.assertNotEqual(below["P"], base["P"])

    def test_analysis_and_note_expose_provenance(self):
        analysis = analyze_soil(
            25, 10, 40, 5.5, crop="maize",
            texture_class="clay", soc_pct=0.5,
        )
        n_item = next(item for item in analysis if item["col"] == "N")
        self.assertTrue(n_item["soil_conditioning"]["applied"])
        self.assertEqual(n_item["soil_conditioning"]["review_status"], "unverified")
        recs = recommend_soil(analysis)
        n_rec = next(rec for rec in recs if rec["element"] == "Nitrogen")
        k_rec = next(rec for rec in recs if rec["element"] == "Potassium")
        self.assertIn("unverified", n_rec["solution"])
        self.assertIn("unverified", k_rec["solution"])

    def test_unknown_woreda_falls_through(self):
        self.assertEqual(
            get_bands("maize", woreda="No Such Woreda"), get_bands("maize"))

    def test_zone_soc_default_only_when_active(self):
        with mock.patch.dict(
                os.environ, {"SOIL_CONDITIONING_ENABLED": "true"}, clear=False):
            zone_bands = get_bands("maize", zone="arid_semi_arid")
        with mock.patch.dict(
                os.environ, {"SOIL_CONDITIONING_ENABLED": "false"}, clear=False):
            plain = get_bands("maize", zone="arid_semi_arid")
        self.assertEqual(zone_bands["N"][0], plain["N"][0] + 20)
        self.assertEqual(zone_bands["N"][1], plain["N"][1] + 20)


class TestConditioningApi(unittest.TestCase):
    def test_reference_endpoint(self):
        resp = client.get("/api/soil-conditioning")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["meta"]["review_status"], "unverified")
        self.assertEqual(len(body["texture_classes"]), 3)
        self.assertEqual(len(body["soc_classes"]), 4)
        self.assertEqual(body["woreda_count"], 0)

    def test_predict_omits_block_without_inputs(self):
        resp = client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.5,
                  "crop": "maize", "use_llm": False},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("soil_conditioning", resp.json())

    def test_predict_applies_and_exposes_conditioning(self):
        resp = client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.0,
                  "crop": "maize", "use_llm": False,
                  "texture_class": "clay", "soc_pct": 0.5},
        )
        self.assertEqual(resp.status_code, 200)
        block = resp.json()["soil_conditioning"]
        self.assertEqual(block["texture_class"], "clay")
        self.assertEqual(block["soc_class"], "very_low")
        self.assertEqual(
            {entry["col"] for entry in block["applied"]}, {"N", "P", "K"})

    def test_predict_rejects_unknown_class(self):
        resp = client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.5,
                  "texture_class": "gravel", "use_llm": False},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unknown texture class", resp.json()["detail"])

    def test_save_report_persists_conditioning_context(self):
        payload = {
            "plot": "E test plot",
            "N": 25, "P": 10, "K": 40, "ph": 5.0,
            "fertility_status": "Poor",
            "recommendations": [],
            "crop": "maize",
            "texture_class": "clay",
            "soc_pct": 0.5,
            "soc_class": "very_low",
            "woreda": "Ada'a",
        }
        saved = client.post("/api/reports", json=payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        report_id = saved.json()["id"]
        detail = client.get(f"/api/reports/{report_id}")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()["report"]
        self.assertEqual(body["texture_class"], "clay")
        self.assertEqual(body["soc_pct"], 0.5)
        self.assertEqual(body["soc_class"], "very_low")
        self.assertEqual(body["woreda"], "Ada'a")
        columns = {
            row["name"]
            for row in db._connect().execute(
                "SELECT name FROM pragma_table_info('reports')")
        }
        for column in ("texture_class", "soc_pct", "soc_class", "woreda"):
            self.assertIn(column, columns)


class TestConditioningCost(unittest.TestCase):
    def test_cost_recomputed_with_conditioned_bands(self):
        base = fertilizer_cost.estimate(25, 10, 40, 5.5, crop="maize")
        adjusted = fertilizer_cost.estimate(
            25, 10, 40, 5.5, crop="maize",
            texture_class="clay", soc_pct=0.5,
        )
        self.assertGreater(adjusted["total_per_ha"], base["total_per_ha"])


if __name__ == "__main__":
    unittest.main()
