"""Enhancement C: Ethiopian crop x region x variety advisory bands."""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

_tmpdir = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmpdir.name) / "test_variety_bands.db"

import api  # noqa: E402
import db  # noqa: E402
import fertilizer_cost  # noqa: E402
import variety_bands  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import analyze_soil, get_bands  # noqa: E402

client = TestClient(api.app)


class TestVarietyData(unittest.TestCase):
    def test_dataset_has_ten_unverified_entries(self):
        entries = variety_bands.list_varieties()
        self.assertEqual(len(entries), 10)
        self.assertEqual(variety_bands.meta()["review_status"], "unverified")
        self.assertTrue(all(e["review_status"] == "unverified" for e in entries))

    def test_crop_and_region_aliases(self):
        self.assertEqual(variety_bands.canonical_crop("Faba Bean"), "faba_bean")
        self.assertEqual(variety_bands.canonical_crop("field pea"), "faba_bean")
        self.assertEqual(
            variety_bands.canonical_region("Arsi/Bale"), "arsi_bale")
        self.assertEqual(
            variety_bands.canonical_region("SNNPR"), "snnpr")

    def test_resolve_is_case_insensitive_and_region_aware(self):
        entry = variety_bands.resolve_variety(
            "maize", region="Oromia", variety=" bh-660 ")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["id"], "maize_oromia_bh660")
        self.assertEqual(
            entry["bands"]["N"], [160.0, 240.0])
        self.assertIsNone(
            variety_bands.resolve_variety(
                "maize", region="SNNPR", variety="BH-660"))
        self.assertIsNone(
            variety_bands.resolve_variety("wheat", variety="BH-660"))

    def test_api_lists_and_filters_varieties(self):
        resp = client.get("/api/varieties")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["varieties"]), 10)
        self.assertEqual(body["meta"]["review_status"], "unverified")
        wheat = client.get(
            "/api/varieties", params={"crop": "wheat"}).json()["varieties"]
        self.assertEqual({v["region"] for v in wheat}, {"arsi_bale", "tigray"})


class TestVarietyEngine(unittest.TestCase):
    def test_variety_band_precedes_crop_and_zone_bands(self):
        crop_bands = get_bands("maize")
        variety = get_bands(
            "maize", zone="nitisols", region="Oromia", variety="BH-660")
        self.assertNotEqual(variety, crop_bands)
        self.assertEqual(variety["N"], [160.0, 240.0])

    def test_variety_analysis_marks_source_and_band(self):
        analysis = analyze_soil(
            100, 30, 250, 6.0, crop="maize", region="Oromia",
            variety="BH-660")
        by_col = {item["col"]: item for item in analysis}
        self.assertEqual(tuple(by_col["N"]["band"]), (160.0, 240.0))
        self.assertEqual(by_col["N"]["deficit"], 60.0)
        self.assertEqual(by_col["N"]["bands_source"], "variety")


class TestVarietyApi(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_variety_api.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def test_predict_non_ml_crop_with_variety(self):
        resp = client.post("/api/predict", json={
            "N": 100, "P": 30, "K": 250, "ph": 6.0,
            "crop": "wheat", "region": "Arsi/Bale", "variety": "Danda'a",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["variety"]["id"], "wheat_arsi_bale_bread")
        self.assertEqual(
            body["variety"]["bands"]["N"], [140.0, 220.0])
        n = next(r for r in body["recommendations"] if r["element"] == "Nitrogen")
        self.assertEqual(n["ideal_range"], "140-220 kg/ha")

    def test_predict_variety_requires_crop_and_must_match(self):
        base = {"N": 100, "P": 30, "K": 250, "ph": 6.0}
        self.assertEqual(client.post("/api/predict", json={
            **base, "variety": "BH-660"}).status_code, 400)
        self.assertEqual(client.post("/api/predict", json={
            **base, "crop": "maize", "variety": "not-a-variety"}).status_code, 400)
        self.assertEqual(client.post("/api/predict", json={
            **base, "crop": "maize", "region": "SNNPR",
            "variety": "BH-660"}).status_code, 400)

    def test_save_and_cost_keep_variety(self):
        resp = client.post("/api/reports", json={
            "plot": "Variety Field", "N": 100, "P": 30, "K": 250, "ph": 6.0,
            "fertility_status": "Poor", "recommendations": [],
            "crop": "maize", "region": "Oromia", "variety": "BH-660",
        })
        self.assertEqual(resp.status_code, 200)
        report_id = resp.json()["id"]
        detail = client.get(f"/api/reports/{report_id}").json()["report"]
        self.assertEqual(detail["variety"], "BH-660")
        self.assertEqual(detail["region"], "Oromia")

        cost = client.get(f"/api/reports/{report_id}/cost-estimate").json()
        direct = fertilizer_cost.estimate(
            100, 30, 250, 6.0, crop="maize", region="Oromia",
            variety="BH-660")
        self.assertEqual(cost["total_per_ha"], direct["total_per_ha"])

    def test_save_rejects_unknown_variety(self):
        resp = client.post("/api/reports", json={
            "plot": "Bad Variety", "N": 100, "P": 30, "K": 250, "ph": 6.0,
            "fertility_status": "Poor", "recommendations": [],
            "crop": "maize", "variety": "not-a-variety",
        })
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
