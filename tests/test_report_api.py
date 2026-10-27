"""API tests for Phase 4 report detail + cost-estimate endpoints."""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import db  # noqa: E402
import fertilizer_cost  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

SAMPLE = {"N": 20.0, "P": 10.0, "K": 120.0, "ph": 4.8}


class ReportApiTestCase(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_report_api.db"
        db.init_db()
        self.farmer_id = db.create_farmer(
            "Abebe Getachew", region="Oromia-Arsi", lang="en")
        self.plot_id = db.create_plot(
            self.farmer_id, "North Ridge", farm_size_ha=2.1,
            latitude=7.95, longitude=39.13)
        self.report_id = db.save_report(
            "North Ridge", SAMPLE["N"], SAMPLE["P"], SAMPLE["K"],
            SAMPLE["ph"], "Low", [{"element": "Nitrogen"}],
            crop="Wheat", farmer_id=self.farmer_id, plot_id=self.plot_id,
            source="manual",
        )

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()


class TestReportDetail(ReportApiTestCase):
    def test_report_detail_shape(self):
        resp = client.get(f"/api/reports/{self.report_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["report"]["id"], self.report_id)
        self.assertEqual(body["report"]["crop"], "Wheat")
        self.assertIsInstance(body["report"]["recommendations"], list)
        self.assertEqual(body["farmer"]["name"], "Abebe Getachew")
        self.assertEqual(body["farmer"]["region"], "Oromia-Arsi")
        self.assertEqual(body["plot"]["name"], "North Ridge")
        self.assertEqual(body["plot"]["farm_size_ha"], 2.1)
        self.assertIsNone(body["outcome"])

    def test_report_detail_includes_outcome_when_present(self):
        db.upsert_outcome(self.report_id, implementation_notes="Applied urea",
                          expected_yield=25.0, actual_yield=27.0)
        body = client.get(f"/api/reports/{self.report_id}").json()
        self.assertIsNotNone(body["outcome"])
        self.assertEqual(body["outcome"]["expected_yield"], 25.0)
        self.assertEqual(body["outcome"]["actual_yield"], 27.0)

    def test_report_detail_unlinked_farmer_ok(self):
        bare_id = db.save_report(
            "Bare Plot", 50.0, 25.0, 120.0, 6.5, "High", [])
        body = client.get(f"/api/reports/{bare_id}").json()
        self.assertIsNone(body["farmer"])
        self.assertIsNone(body["plot"])

    def test_report_detail_404(self):
        resp = client.get("/api/reports/99999")
        self.assertEqual(resp.status_code, 404)


class TestCostEstimateApi(ReportApiTestCase):
    def test_cost_estimate_shape_and_math(self):
        resp = client.get(f"/api/reports/{self.report_id}/cost-estimate")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["currency"], "ETB")
        self.assertEqual(len(body["lines"]), 4)
        self.assertEqual(
            body["total_per_ha"],
            round(sum(l["subtotal"] for l in body["lines"]), 2),
        )
        self.assertGreater(body["total_per_ha"], 0)
        self.assertEqual(body["disclaimer_key"], "costDisclaimer")
        self.assertIn("Indicative", body["disclaimer"])

    def test_cost_uses_stored_inputs_and_plot_size(self):
        body = client.get(f"/api/reports/{self.report_id}/cost-estimate").json()
        direct = fertilizer_cost.estimate(
            SAMPLE["N"], SAMPLE["P"], SAMPLE["K"], SAMPLE["ph"],
            crop="Wheat", size_ha=2.1)
        self.assertEqual(body["total_per_ha"], direct["total_per_ha"])
        self.assertEqual(body["size_ha"], 2.1)
        self.assertEqual(body["total_for_plot"],
                         round(direct["total_per_ha"] * 2.1, 2))

    def test_cost_adequate_report_totals_zero(self):
        # Values inside the generic EthioSIS bands: N 60-100, P 78-117
        # (lab 30-45 mg/kg x 2.6), K 78.2-156.4, pH 6.5-7.5 -> no purchase.
        good_id = db.save_report(
            "North Ridge", 80.0, 90.0, 120.0, 6.8, "High", [],
            farmer_id=self.farmer_id, plot_id=self.plot_id)
        body = client.get(f"/api/reports/{good_id}/cost-estimate").json()
        self.assertEqual(body["total_per_ha"], 0.0)
        for line in body["lines"]:
            self.assertFalse(line["purchase"])

    def test_cost_estimate_404(self):
        resp = client.get("/api/reports/99999/cost-estimate")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
