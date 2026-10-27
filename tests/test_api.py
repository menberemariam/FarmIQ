import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config

# Point the API at a throwaway DB before importing it.
_tmpdir = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmpdir.name) / "test_api.db"

import db  # noqa: E402
import pandas as pd  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import analyze_soil  # noqa: E402

client = TestClient(api.app)


class TestApiHealthAndCrops(unittest.TestCase):
    def test_health(self):
        resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_health_reports_model_version(self):
        resp = client.get("/api/health")
        body = resp.json()
        self.assertEqual(body["models"], "loaded")
        self.assertEqual(body["model_version"], api.MODEL_VERSION)

    def test_health_degraded_when_models_unloadable(self):
        api._models.cache_clear()
        try:
            with mock.patch("api.joblib.load", side_effect=OSError("missing model")):
                resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "degraded")
            self.assertEqual(resp.json()["models"], "error")
        finally:
            api._models.cache_clear()

    def test_health_omits_llm_contract_by_default(self):
        previous = config.LLM_TELEMETRY
        config.LLM_TELEMETRY = False
        try:
            body = client.get("/api/health").json()
            self.assertNotIn("llm_contract", body)
        finally:
            config.LLM_TELEMETRY = previous

    def test_health_reports_llm_contract_when_enabled(self):
        previous = config.LLM_TELEMETRY
        config.LLM_TELEMETRY = True
        try:
            body = client.get("/api/health").json()
            self.assertEqual(
                set(body["llm_contract"]),
                {"llm_calls", "llm_parse_failures", "llm_truncations",
                 "llm_elements_recovered", "llm_contract_rejects"},
            )
        finally:
            config.LLM_TELEMETRY = previous

    def test_crops_returns_22_real_bands(self):
        resp = client.get("/api/crops")
        self.assertEqual(resp.status_code, 200)
        crops = resp.json()["crops"]
        self.assertEqual(len(crops), 22)
        rice = next(c for c in crops if c["name"] == "rice")
        self.assertEqual(sorted(rice["bands"].keys()), sorted(config.FEATURE_COLS))
        self.assertEqual(rice["scientific_name"], "Oryza sativa")
        self.assertIsInstance(rice["temperature"], float)
        self.assertIsInstance(rice["rainfall"], float)


class TestApiPredict(unittest.TestCase):
    def test_predict_structure(self):
        resp = client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "crop": "rice"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn(body["fertility_status"], ["Good", "Moderate", "Poor"])
        self.assertTrue(0 <= body["confidence"] <= 100)
        self.assertEqual(len(body["recommendations"]), 4)
        for rec in body["recommendations"]:
            self.assertEqual(
                set(rec.keys()),
                {"element", "status", "deficit", "value", "ideal_range",
                 "solution", "source", "lang"},
            )
            self.assertIn(rec["status"], ["deficient", "ample", "adequate"])
            self.assertIsInstance(rec["value"], float)
            if rec["element"] == "pH":
                self.assertNotIn("kg/ha", rec["ideal_range"])
            else:
                self.assertIn("kg/ha", rec["ideal_range"])
            self.assertGreater(len(rec["solution"]), 50)
        self.assertEqual(len(body["crops"]), 3)
        for crop in body["crops"]:
            self.assertIn(crop["crop"], set(pd.read_csv(config.CROP_DATA_CSV)["label"]))
            self.assertIsInstance(crop["scientific_name"], str)
            self.assertTrue(0 <= crop["confidence"] <= 100)
            self.assertIsInstance(crop["temperature"], float)
            self.assertIsInstance(crop["rainfall"], float)

    def test_predict_unknown_crop_rejected(self):
        resp = client.post("/api/predict", json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "crop": "wheat"})
        self.assertEqual(resp.status_code, 400)

    def test_predict_validation(self):
        resp = client.post("/api/predict", json={"N": -5, "P": 10, "K": 40, "ph": 5.5})
        self.assertEqual(resp.status_code, 422)

    def test_predict_amharic(self):
        resp = client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "crop": "rice", "lang": "am"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["lang"], "am")
        for rec in body["recommendations"]:
            self.assertEqual(rec["lang"], "am")
            self.assertRegex(rec["solution"], r"[\u1200-\u137F]")
            self.assertIn(rec["status"], ["deficient", "ample", "adequate"])

    def test_predict_lang_normalization(self):
        """'am-ET' must normalize to 'am'; 'xx' must be rejected."""
        resp = client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "lang": "am-ET"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["lang"], "am")

        resp = client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "lang": "xx"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_predict_no_warnings_when_in_range(self):
        resp = client.post("/api/predict", json={"N": 25, "P": 10, "K": 40, "ph": 5.5})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["warnings"], [])
        self.assertFalse(body["llm_used"])
        self.assertEqual(body["model_version"], api.MODEL_VERSION)

    def test_predict_out_of_range_warning(self):
        resp = client.post("/api/predict", json={"N": 155, "P": 10, "K": 40, "ph": 5.5})
        self.assertEqual(resp.status_code, 200)
        warnings = resp.json()["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["field"], "N")
        self.assertEqual(warnings[0]["value"], 155)
        self.assertEqual(warnings[0]["range"], [0, 140])
        self.assertIn("N=155", warnings[0]["message"])

    def test_predict_out_of_range_warning_amharic(self):
        resp = client.post(
            "/api/predict",
            json={"N": 155, "P": 10, "K": 40, "ph": 5.5, "lang": "am"},
        )
        self.assertEqual(resp.status_code, 200)
        message = resp.json()["warnings"][0]["message"]
        self.assertRegex(message, r"[\u1200-\u137F]")

    def test_predict_llm_without_key_falls_back(self):
        with mock.patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            resp = client.post(
                "/api/predict",
                json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "use_llm": True},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["llm_used"])
        for rec in body["recommendations"]:
            self.assertEqual(rec["source"], "rule-based")

    def test_predict_llm_merged_when_valid(self):
        def fake_llm(analysis, api_key, lang="en"):
            out = {}
            for item in analysis:
                lo, hi = item["band"]
                out[item["element"]] = {
                    "status": item["status"],
                    "solution": (
                        f"Keep {item['element']} inside {lo}-{hi} "
                        f"(difference {item['deficit']:.1f}) using validated inputs."
                    ),
                }
            return out

        with mock.patch.object(api, "get_llm_recommendations", side_effect=fake_llm):
            resp = client.post(
                "/api/predict",
                json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "use_llm": True},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["llm_used"])
        for rec in body["recommendations"]:
            self.assertEqual(rec["source"], "llm")

    def test_predict_llm_error_never_breaks_request(self):
        with mock.patch.object(
            api, "get_llm_recommendations", side_effect=RuntimeError("boom")
        ):
            resp = client.post(
                "/api/predict",
                json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "use_llm": True},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["llm_used"])
        self.assertEqual(len(body["recommendations"]), 4)


class TestApiReports(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_report_round_trip_and_improvement(self):
        report = {
            "plot": "Api Field", "N": 25, "P": 10, "K": 40, "ph": 5.5,
            "fertility_status": "Poor",
            "recommendations": [{"element": "Nitrogen", "status": "deficient",
                                 "deficit": 10.0, "solution": "Apply urea.", "source": "rule-based"}],
            "crop": "rice", "temperature": 25.0, "humidity": 80.0,
            "rainfall": 200.0, "crops": [{"crop": "rice", "confidence": 0.9}],
        }
        resp = client.post("/api/reports", json=report)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["saved"])

        resp = client.post("/api/reports", json={**report, "N": 45, "P": 18, "K": 90, "ph": 6.4,
                                                 "fertility_status": "Moderate"})
        self.assertEqual(resp.status_code, 200)

        reports = client.get("/api/reports", params={"plot": "Api Field"}).json()["reports"]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["fertility_status"], "Moderate")

        plots = client.get("/api/reports/plots").json()["plots"]
        self.assertEqual(plots[0]["plot"], "Api Field")

        impr = client.get("/api/reports/improvement", params={"plot": "Api Field"}).json()
        self.assertEqual(impr["deltas"], {"N": 20.0, "P": 8.0, "K": 50.0, "ph": 0.9})
        self.assertEqual(impr["old_status"], "Poor")
        self.assertEqual(impr["new_status"], "Moderate")

    def test_improvement_404_for_single_report(self):
        client.post("/api/reports", json={
            "plot": "Solo", "N": 25, "P": 10, "K": 40, "ph": 5.5,
            "fertility_status": "Poor",
            "recommendations": [{"element": "Nitrogen", "status": "deficient",
                                 "deficit": 10.0, "solution": "Apply urea.", "source": "rule-based"}],
        })
        resp = client.get("/api/reports/improvement", params={"plot": "Solo"})
        self.assertEqual(resp.status_code, 404)

    def test_report_validation_rejects_bad_values(self):
        base = {
            "plot": "Validation Field", "N": 25, "P": 10, "K": 40, "ph": 5.5,
            "fertility_status": "Poor",
            "recommendations": [{"element": "Nitrogen", "status": "deficient",
                                 "deficit": 10.0, "solution": "Apply urea.", "source": "rule-based"}],
        }
        bad_payloads = [
            {**base, "ph": 20},
            {**base, "N": -1},
            {**base, "fertility_status": "Excellent"},
            {**base, "plot": ""},
        ]
        for payload in bad_payloads:
            resp = client.post("/api/reports", json=payload)
            self.assertEqual(resp.status_code, 422, payload)


class TestCors(unittest.TestCase):
    """The frontend (localhost:3000) must be able to call this API from a
    browser. Without CORS headers every fetch is blocked, so these tests are
    the regression guard for the integration bug."""

    def test_preflight_allowed_origin(self):
        resp = client.options(
            "/api/predict",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Access-Control-Allow-Origin", resp.headers)

    def test_get_response_has_allow_origin(self):
        resp = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["access-control-allow-origin"], "http://localhost:3000")

    def test_disallowed_origin_gets_no_allow_origin(self):
        resp = client.get("/api/health", headers={"Origin": "http://evil.example"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("access-control-allow-origin", resp.headers)


class TestI18n(unittest.TestCase):
    def test_all_keys_translated_in_all_languages(self):
        from i18n import STRINGS

        en_keys = set(STRINGS["en"].keys())
        for lang, strings in STRINGS.items():
            missing = en_keys - set(strings.keys())
            self.assertFalse(missing, f"{lang} missing keys: {missing}")

    def test_fallback_to_english(self):
        from i18n import translate

        self.assertEqual(translate("hi", "no_such_key"), "no_such_key")


if __name__ == "__main__":
    unittest.main()
