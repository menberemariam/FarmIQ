"""Phase 5: Ethiopian crop suitability overlay + staple-matrix endpoints.

GET  /api/crops/ethiopian  — full 34-crop matrix (S1/S2/N bands), additive
POST /api/crops/suitability — rule evaluation for staples or any matrix crop
POST /api/predict — suitability field on crops[] only when the user
                     supplies temperature+rainfall (additive; ML contract
                     and the existing /api/crops 22-crop shape stay intact).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import api  # noqa: E402
import ethiopian_standards as es  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)


class TestEthiopianCropsEndpoint(unittest.TestCase):
    def test_returns_34_crops_with_bands(self):
        resp = client.get("/api/crops/ethiopian")
        self.assertEqual(resp.status_code, 200)
        crops = resp.json()["crops"]
        self.assertEqual(len(crops), 34)
        barley = next(c for c in crops if c["name"] == "barley")
        self.assertEqual(barley["name_am"], "ገብስ (barley)")
        self.assertEqual(barley["category"], "cereal")
        self.assertIn("Cool highlands", barley["regions"])
        for cls in ("S1", "S2", "N"):
            self.assertIn(cls, barley)
            for param in ("ph", "temperature", "rainfall"):
                self.assertIn(param, barley[cls])

    def test_staples_flagged_not_in_ml_model(self):
        crops = client.get("/api/crops/ethiopian").json()["crops"]
        staples = [c["name"] for c in crops if not c["in_ml_model"]]
        self.assertEqual(staples, sorted(es.ethiopian_staples()))
        self.assertEqual(len(staples), 12)
        # Wheat/teff/enset are staples here; maize/rice are ML crops
        wheat = next(c for c in crops if c["name"] == "wheat")
        self.assertFalse(wheat["in_ml_model"])
        maize = next(c for c in crops if c["name"] == "maize")
        self.assertTrue(maize["in_ml_model"])

    def test_existing_ml_endpoint_contract_unchanged(self):
        crops = client.get("/api/crops").json()["crops"]
        self.assertEqual(len(crops), 22)
        rice = next(c for c in crops if c["name"] == "rice")
        self.assertEqual(sorted(rice["bands"].keys()), ["K", "N", "P", "ph"])
        # ML endpoint entries stay lean — no suitability matrix leaked in
        self.assertNotIn("S1", rice)


class TestSuitabilityEndpoint(unittest.TestCase):
    def test_requires_at_least_one_parameter(self):
        resp = client.post("/api/crops/suitability", json={})
        self.assertEqual(resp.status_code, 422)

    def test_defaults_to_staples(self):
        resp = client.post(
            "/api/crops/suitability",
            json={"ph": 6.5, "temperature": 15, "rainfall": 800},
        )
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(
            sorted(r["crop"] for r in results), sorted(es.ethiopian_staples())
        )
        barley = next(r for r in results if r["crop"] == "barley")
        self.assertEqual(barley["class"], "S1")
        self.assertTrue(all("kg" not in r for r in barley["reasons"]))

    def test_teff_fails_drought_conditions(self):
        # Rainfall 100mm is below teff's strict N-exclusion floor (< 400).
        resp = client.post(
            "/api/crops/suitability",
            json={"crops": ["teff"], "ph": 6.5, "temperature": 22, "rainfall": 100},
        )
        result = resp.json()["results"][0]
        self.assertEqual(result["class"], "N")
        self.assertTrue(any("rainfall" in r for r in result["reasons"]))

    def test_unknown_crop_rejected(self):
        resp = client.post(
            "/api/crops/suitability",
            json={"crops": ["blueberry"], "ph": 6.5},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("blueberry", resp.json()["detail"])

    def test_explicit_ml_crops_allowed(self):
        resp = client.post(
            "/api/crops/suitability",
            json={"crops": ["wheat", "rice"], "ph": 6.8, "temperature": 18, "rainfall": 900},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(r["crop"] for r in resp.json()["results"]), ["rice", "wheat"])

    def test_only_ph_evaluated_when_climate_omitted(self):
        resp = client.post(
            "/api/crops/suitability",
            json={"crops": ["potato"], "ph": 4.5},
        )
        result = resp.json()["results"][0]
        params_checked = [c["param"] for c in result["checks"]]
        self.assertEqual(params_checked, ["ph"])


class TestPredictSuitabilityOverlay(unittest.TestCase):
    def test_overlay_present_when_climate_provided(self):
        resp = client.post(
            "/api/predict",
            json={
                "N": 80, "P": 40, "K": 120, "ph": 6.8,
                "temperature": 18, "rainfall": 900,
            },
        )
        self.assertEqual(resp.status_code, 200)
        for rec in resp.json()["crops"]:
            self.assertIn("suitability", rec)
            self.assertIn(rec["suitability"]["class"], ("S1", "S2", "S3", "N"))
            self.assertIsInstance(rec["suitability"]["reasons"], list)

    def test_overlay_absent_without_climate(self):
        resp = client.post(
            "/api/predict",
            json={"N": 80, "P": 40, "K": 120, "ph": 6.8},
        )
        for rec in resp.json()["crops"]:
            self.assertNotIn("suitability", rec)

    def test_ml_contract_fields_still_present(self):
        resp = client.post(
            "/api/predict",
            json={
                "N": 80, "P": 40, "K": 120, "ph": 6.8,
                "temperature": 18, "rainfall": 900, "crop": "rice",
            },
        )
        body = resp.json()
        self.assertEqual(body["fertility_status"], "Good")
        self.assertEqual(len(body["crops"]), 3)
        for rec in body["crops"]:
            # every pre-existing field survives the additive overlay
            for key in ("crop", "scientific_name", "confidence", "temperature", "rainfall"):
                self.assertIn(key, rec)
            self.assertIn("suitability", rec)


if __name__ == "__main__":
    unittest.main()
