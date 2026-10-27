import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config

import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import analyze_soil  # noqa: E402

client = TestClient(api.app)

VALID_REPORT = {
    "plot": "North Ridge", "N": 25, "P": 10, "K": 40, "ph": 5.5,
    "fertility_status": "Poor",
    "recommendations": [{"element": "Nitrogen", "status": "deficient",
                         "deficit": 10.0, "solution": "Apply urea.",
                         "source": "rule-based"}],
    "crop": "rice", "temperature": 25.0, "humidity": 80.0, "rainfall": 200.0,
}


class AgentApiTestCase(unittest.TestCase):
    """Each test gets its own throwaway DB; config.DB_PATH is restored so
    other test modules (test_api) keep their own database."""

    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_agent_api.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def _farmer(self, name="Abebe Getachew", **extra):
        resp = client.post("/api/farmers", json={"name": name, **extra})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["id"]

    def _plot(self, farmer_id, name="North Ridge", **extra):
        resp = client.post("/api/plots", json={"farmer_id": farmer_id, "name": name, **extra})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["id"]


class TestFarmersEndpoints(AgentApiTestCase):
    def test_create_and_list_farmers(self):
        fid = self._farmer(phone="0911000000", region="Oromia", lang="am")
        farmer = client.get("/api/farmers").json()["farmers"]
        self.assertEqual(len(farmer), 1)
        self.assertEqual(farmer[0]["id"], fid)
        self.assertEqual(farmer[0]["name"], "Abebe Getachew")
        self.assertEqual(farmer[0]["lang"], "am")
        self.assertEqual(farmer[0]["plot_count"], 0)
        self.assertEqual(farmer[0]["report_count"], 0)

    def test_create_farmer_validation(self):
        resp = client.post("/api/farmers", json={"name": ""})
        self.assertEqual(resp.status_code, 422)
        resp = client.post("/api/farmers", json={"name": "Abebe", "lang": "xx"})
        # lang normalizes to "en" rather than rejecting.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["farmer"]["lang"], "en")

    def test_farmer_detail_404_unknown(self):
        resp = client.get("/api/farmers/9999")
        self.assertEqual(resp.status_code, 404)

    def test_farmer_detail_shape(self):
        fid = self._farmer(region="Oromia")
        pid = self._plot(fid, farm_size_ha=2.1, latitude=7.95, longitude=39.13)
        resp = client.post("/api/reports", json={**VALID_REPORT, "farmer_id": fid, "plot_id": pid})
        report_id = resp.json()["id"]
        client.post(f"/api/reports/{report_id}/outcome", json={
            "implementation_notes": "Urea split applied",
            "expected_yield": 3.0, "yield_unit": "t/ha",
        })

        detail = client.get(f"/api/farmers/{fid}").json()
        self.assertEqual(detail["farmer"]["name"], "Abebe Getachew")
        self.assertEqual(detail["farmer"]["region"], "Oromia")

        self.assertEqual(len(detail["plots"]), 1)
        plot = detail["plots"][0]
        self.assertEqual(plot["name"], "North Ridge")
        self.assertEqual(plot["farm_size_ha"], 2.1)
        self.assertEqual(plot["report_count"], 1)
        self.assertEqual(plot["latest_status"], "Poor")

        self.assertEqual(len(detail["reports"]), 1)
        report = detail["reports"][0]
        self.assertEqual(report["farmer_id"], fid)
        self.assertEqual(report["plot_id"], pid)
        self.assertEqual(report["outcome"]["implementation_notes"], "Urea split applied")
        self.assertEqual(report["recommendations"][0]["element"], "Nitrogen")


class TestPlotsEndpoint(AgentApiTestCase):
    def test_create_plot_404_unknown_farmer(self):
        resp = client.post("/api/plots", json={"farmer_id": 9999, "name": "Ghost"})
        self.assertEqual(resp.status_code, 404)

    def test_create_plot_returns_row(self):
        fid = self._farmer()
        resp = client.post("/api/plots", json={
            "farmer_id": fid, "name": "South Flat", "farm_size_ha": 0.75,
            "latitude": 8.0, "longitude": 39.0, "previous_crop": "maize",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["saved"] if "saved" in body else True)
        plot = body["plot"]
        self.assertEqual(plot["farmer_id"], fid)
        self.assertEqual(plot["previous_crop"], "maize")

    def test_create_plot_validation(self):
        fid = self._farmer()
        for payload in (
            {"farmer_id": fid, "name": ""},
            {"farmer_id": fid, "name": "X", "latitude": 91},
            {"farmer_id": fid, "name": "X", "longitude": -200},
        ):
            resp = client.post("/api/plots", json=payload)
            self.assertEqual(resp.status_code, 422, payload)


class TestReportAgentLinking(AgentApiTestCase):
    def test_report_with_farmer_and_plot(self):
        fid = self._farmer()
        pid = self._plot(fid)
        resp = client.post("/api/reports", json={
            **VALID_REPORT, "farmer_id": fid, "plot_id": pid, "source": "manual",
        })
        self.assertEqual(resp.status_code, 200, resp.text)

        reports = client.get("/api/reports", params={"farmer_id": fid}).json()["reports"]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["farmer_id"], fid)
        self.assertEqual(reports[0]["plot_id"], pid)
        self.assertEqual(reports[0]["source"], "manual")

    def test_report_without_agent_fields_still_saves(self):
        resp = client.post("/api/reports", json=VALID_REPORT)
        self.assertEqual(resp.status_code, 200)
        report = client.get("/api/reports").json()["reports"][0]
        self.assertIsNone(report["farmer_id"])
        self.assertIsNone(report["plot_id"])
        self.assertEqual(report["source"], "manual")

    def test_report_rejects_unknown_farmer(self):
        resp = client.post("/api/reports", json={**VALID_REPORT, "farmer_id": 9999})
        self.assertEqual(resp.status_code, 404)

    def test_report_rejects_unknown_plot(self):
        fid = self._farmer()
        resp = client.post("/api/reports", json={**VALID_REPORT, "farmer_id": fid, "plot_id": 9999})
        self.assertEqual(resp.status_code, 404)

    def test_report_rejects_plot_owned_by_other_farmer(self):
        f1 = self._farmer("Farmer One")
        f2 = self._farmer("Farmer Two")
        pid = self._plot(f1, "One Field")
        resp = client.post("/api/reports", json={**VALID_REPORT, "farmer_id": f2, "plot_id": pid})
        self.assertEqual(resp.status_code, 400)

    def test_report_source_literal_enforced(self):
        resp = client.post("/api/reports", json={**VALID_REPORT, "source": "telepathy"})
        self.assertEqual(resp.status_code, 422)

    def test_farmer_filter_on_reports(self):
        f1 = self._farmer("Farmer One")
        f2 = self._farmer("Farmer Two")
        client.post("/api/reports", json={**VALID_REPORT, "plot": "A", "farmer_id": f1})
        client.post("/api/reports", json={**VALID_REPORT, "plot": "B", "farmer_id": f2})
        reports = client.get("/api/reports", params={"farmer_id": f1}).json()["reports"]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["plot"], "A")


class TestOutcomeEndpoint(AgentApiTestCase):
    def test_outcome_404_unknown_report(self):
        resp = client.post("/api/reports/9999/outcome", json={"expected_yield": 3.0})
        self.assertEqual(resp.status_code, 404)
        resp = client.get("/api/reports/9999/outcome")
        self.assertEqual(resp.status_code, 404)

    def test_outcome_get_before_save_is_null(self):
        rid = client.post("/api/reports", json=VALID_REPORT).json()["id"]
        resp = client.get(f"/api/reports/{rid}/outcome")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["outcome"])

    def test_outcome_upsert_round_trip(self):
        rid = client.post("/api/reports", json=VALID_REPORT).json()["id"]

        resp = client.post(f"/api/reports/{rid}/outcome", json={
            "implementation_notes": "Urea split", "implemented_at": "2026-05-01",
            "expected_yield": 3.0, "yield_unit": "t/ha",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["saved"])

        resp = client.post(f"/api/reports/{rid}/outcome", json={
            "implementation_notes": "Urea split", "implemented_at": "2026-05-01",
            "expected_yield": 3.0, "actual_yield": 2.7, "yield_unit": "t/ha",
            "harvest_at": "2026-09-15",
        })
        self.assertEqual(resp.status_code, 200)

        # One report, one outcome row — replaced, not duplicated.
        self.assertEqual(len(client.get("/api/reports").json()["reports"]), 1)
        outcome = client.get(f"/api/reports/{rid}/outcome").json()["outcome"]
        self.assertEqual(outcome["actual_yield"], 2.7)
        self.assertEqual(outcome["harvest_at"], "2026-09-15")

    def test_outcome_validation(self):
        rid = client.post("/api/reports", json=VALID_REPORT).json()["id"]
        for payload in (
            {"expected_yield": -5},
            {"actual_yield": -1},
        ):
            resp = client.post(f"/api/reports/{rid}/outcome", json=payload)
            self.assertEqual(resp.status_code, 422, payload)


class TestReportDetailMetricsEnrichment(AgentApiTestCase):
    """Streamlit/seed_demo save recommendations without value/ideal_range;
    the printable view must still receive them from GET /api/reports/{id}."""

    def test_detail_fills_missing_metrics(self):
        self.assertNotIn("value", VALID_REPORT["recommendations"][0])
        rid = client.post("/api/reports", json=VALID_REPORT).json()["id"]

        report = client.get(f"/api/reports/{rid}").json()["report"]
        rec = report["recommendations"][0]
        self.assertIn("value", rec)
        self.assertIn("ideal_range", rec)
        self.assertIsInstance(rec["value"], float)
        self.assertIn("kg/ha", rec["ideal_range"])

        expected = next(
            item for item in analyze_soil(25, 10, 40, 5.5, crop="rice")
            if item["element"] == "Nitrogen"
        )
        self.assertEqual(rec["value"], expected["value"])

    def test_detail_keeps_metrics_already_present(self):
        stored = {
            **VALID_REPORT,
            "recommendations": [{
                "element": "Nitrogen", "status": "deficient", "deficit": 10.0,
                "value": 123.0, "ideal_range": "111-133 kg/ha",
                "solution": "Apply urea.", "source": "rule-based",
            }],
        }
        rid = client.post("/api/reports", json=stored).json()["id"]

        rec = client.get(f"/api/reports/{rid}").json()["report"]["recommendations"][0]
        self.assertEqual(rec["value"], 123.0)
        self.assertEqual(rec["ideal_range"], "111-133 kg/ha")

    def test_detail_404_unchanged(self):
        self.assertEqual(client.get("/api/reports/9999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
