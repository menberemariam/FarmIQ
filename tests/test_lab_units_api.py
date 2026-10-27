"""Tests for the dual unit-system input (input_units=kg_ha|lab) on /api/predict.

Lab mode accepts EthioSIS/ESRI laboratory units — total N % (Kjeldahl),
available P mg/kg (Olsen/Mehlich-III), exchangeable K cmolc/kg — and
converts them to the model's kg/ha scale inside the API. Responses always
stay in model units. Default (kg_ha) behaviour must be unchanged.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ethiopian_standards as es  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)


class TestLabUnitsPredict(unittest.TestCase):
    def test_default_echoes_kg_ha(self):
        resp = client.post(
            "/api/predict",
            json={"N": 80, "P": 40, "K": 120, "ph": 6.8},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["input_units"], "kg_ha")

    def test_invalid_units_rejected(self):
        resp = client.post(
            "/api/predict",
            json={"N": 1, "P": 20, "K": 0.3, "ph": 6.5, "input_units": "ppm"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_lab_mode_matches_equivalent_kg_ha_request(self):
        # 0.2% N, 25 mg/kg P, 0.3 cmolc/kg K, pH 6.5
        lab = client.post(
            "/api/predict",
            json={
                "N": 0.2, "P": 25, "K": 0.3, "ph": 6.5,
                "input_units": "lab", "crop": "rice",
            },
        )
        self.assertEqual(lab.status_code, 200)
        lab_body = lab.json()
        self.assertEqual(lab_body["input_units"], "lab")

        n_kg = es.n_pct_to_kg_ha(0.2)   # 80
        k_kg = es.k_cmolc_to_kg_ha(0.3)  # 117.3
        p_kg = es.p_mgkg_to_kg_ha(25)    # 65.0 (2.6 kg/ha per mg/kg)
        kg = client.post(
            "/api/predict",
            json={
                "N": n_kg, "P": p_kg, "K": k_kg, "ph": 6.5,
                "crop": "rice",
            },
        )
        kg_body = kg.json()
        self.assertEqual(lab_body["fertility_status"], kg_body["fertility_status"])
        self.assertEqual(lab_body["confidence"], kg_body["confidence"])
        self.assertEqual(lab_body["recommendations"], kg_body["recommendations"])
        self.assertEqual(lab_body["crops"], kg_body["crops"])

    def test_lab_values_converted_in_recommendation_value(self):
        resp = client.post(
            "/api/predict",
            json={"N": 0.2, "P": 25, "K": 0.3, "ph": 6.5, "input_units": "lab"},
        )
        by_element = {r["element"]: r for r in resp.json()["recommendations"]}
        self.assertAlmostEqual(by_element["Nitrogen"]["value"], 80.0)
        # Lab mg/kg converts at the bulk-density factor (25 mg/kg -> 65 kg/ha)
        self.assertAlmostEqual(by_element["Phosphorus"]["value"], 65.0)
        self.assertAlmostEqual(by_element["Potassium"]["value"], 117.3)
        self.assertAlmostEqual(by_element["pH"]["value"], 6.5)
        # Ideal ranges stay in model units in both modes
        self.assertIn("kg/ha", by_element["Nitrogen"]["ideal_range"])

    def test_lab_mode_out_of_range_warning_uses_converted_value(self):
        # 1.0% total N -> 400 kg/ha available, far beyond the training range
        resp = client.post(
            "/api/predict",
            json={"N": 1.0, "P": 25, "K": 0.3, "ph": 6.5, "input_units": "lab"},
        )
        warnings = resp.json()["warnings"]
        n_warnings = [w for w in warnings if w["field"] == "N"]
        self.assertEqual(len(n_warnings), 1)
        self.assertEqual(n_warnings[0]["range"], [0, 140])
        self.assertAlmostEqual(n_warnings[0]["value"], 400.0)

    def test_zero_lab_values_allowed(self):
        resp = client.post(
            "/api/predict",
            json={"N": 0.05, "P": 5, "K": 0.1, "ph": 5.0, "input_units": "lab"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["fertility_status"])


class TestConvertersAlignWithApi(unittest.TestCase):
    def test_default_n_factor_is_400(self):
        self.assertEqual(es.n_pct_to_kg_ha(0.25), 100.0)

    def test_k_converted_value(self):
        self.assertAlmostEqual(es.k_cmolc_to_kg_ha(0.2), 78.2)
        self.assertAlmostEqual(es.k_cmolc_to_kg_ha(0.4), 156.4)


if __name__ == "__main__":
    unittest.main()
