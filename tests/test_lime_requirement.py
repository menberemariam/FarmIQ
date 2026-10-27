"""Tests for the Ethiopian Lime Requirement integration (Part 1c).

Pins the additive LRF contract:
  * Dataset/lime_requirement.json loads with the five EIAR soil types;
  * canonical_soil_type accepts ids, plural display names and aliases;
  * lime_rate_t reproduces the legacy generic formula byte-for-byte when
    no soil type is given, and applies LRF x (0.84 dolomitic) otherwise;
  * the 5.0 t/ha single-application ceiling splits the program into
    seasons without ever capping the total;
  * engine templates + fertilizer_cost share the one function, so the
    priced lime line always equals the number in the report text;
  * /api/predict gains optional soil_type/lime_type + an additive
    lime_plan block, and rejects unknown soil types with 400;
  * without soil_type every response keeps its legacy shape and numbers.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import db  # noqa: E402
import ethiopian_standards as ethio  # noqa: E402
import fertilizer_cost  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import analyze_soil, recommend_soil  # noqa: E402
import api  # noqa: E402

client = TestClient(api.app)

ACIDIC = {"N": 80.0, "P": 90.0, "K": 120.0, "ph": 4.8}


def _rec(analysis, lang="en"):
    return {r["element"]: r for r in recommend_soil(analysis, lang=lang)}


class TestLimeDataset(unittest.TestCase):
    def test_five_soil_types_with_lrf(self):
        soil_types = ethio.list_soil_types()
        self.assertEqual(
            set(soil_types),
            {"nitisol", "vertisol", "cambisol_luvisol", "andosol", "arenosol"},
        )
        self.assertEqual(ethio.lime_lrf("nitisol"), 4.2)
        self.assertEqual(ethio.lime_lrf("vertisol"), 5.5)
        self.assertEqual(ethio.lime_lrf("cambisol_luvisol"), 2.8)
        self.assertEqual(ethio.lime_lrf("andosol"), 3.5)
        self.assertEqual(ethio.lime_lrf("arenosol"), 1.2)

    def test_meta_ceiling_and_factors(self):
        meta = ethio.lime_meta()
        self.assertEqual(meta["ecce_pct"], 90)
        self.assertEqual(meta["max_single_application_t_ha"], 5.0)
        self.assertEqual(meta["dolomitic_factor"], 0.84)

    def test_canonical_spellings(self):
        for spelling in ("nitisol", "Nitisols", "NITISOL"):
            self.assertEqual(ethio.canonical_soil_type(spelling), "nitisol")
        self.assertEqual(ethio.canonical_soil_type("Vertisols"), "vertisol")
        self.assertEqual(ethio.canonical_soil_type("Andosols"), "andosol")
        self.assertEqual(
            ethio.canonical_soil_type("Arenosols (sandy soils)"), "arenosol")
        self.assertEqual(ethio.canonical_soil_type("Luvisol"), "cambisol_luvisol")
        self.assertEqual(ethio.canonical_soil_type("sandy"), "arenosol")
        self.assertIsNone(ethio.canonical_soil_type("loam"))
        self.assertIsNone(ethio.canonical_soil_type(None))

    def test_zone_defaults_are_hints_only(self):
        self.assertEqual(ethio.zone_soil_default("highland_nitisols"), "nitisol")
        self.assertEqual(ethio.zone_soil_default("Highland Vertisols"), "vertisol")
        self.assertIsNone(ethio.zone_soil_default("unknown"))


class TestLimeRate(unittest.TestCase):
    def test_legacy_formula_unchanged_without_soil_type(self):
        # Same pins as the classic engine/cost tests: 0.69 -> 2.8, 1.7 -> 6.8.
        self.assertEqual(ethio.lime_rate_t(0.69), 2.8)
        self.assertEqual(ethio.lime_rate_t(1.7), 6.8)
        # Cap at 10 t/ha for extreme acidity.
        self.assertEqual(ethio.lime_rate_t(3.0), 10.0)
        self.assertEqual(ethio.lime_rate_t(3.0, soil_type="unknown"), 10.0)

    def test_lrf_rates_per_soil_type(self):
        self.assertEqual(ethio.lime_rate_t(1.0, "nitisol"), 4.2)
        self.assertEqual(ethio.lime_rate_t(1.5, "vertisol"), 8.25)
        self.assertEqual(ethio.lime_rate_t(2.0, "arenosol"), 2.4)
        self.assertEqual(ethio.lime_rate_t(1.0, "andosol"), 3.5)
        self.assertEqual(ethio.lime_rate_t(1.0, "cambisol_luvisol"), 2.8)

    def test_dolomitic_factor(self):
        self.assertEqual(ethio.lime_rate_t(2.0, "nitisol", "dolomitic"), 7.06)
        self.assertEqual(ethio.lime_rate_t(2.0, "nitisol", "calcitic"), 8.4)
        # Unknown lime types fall back to calcitic.
        self.assertEqual(ethio.lime_rate_t(2.0, "nitisol", "bogus"), 8.4)

    def test_zero_and_negative_deficit(self):
        self.assertEqual(ethio.lime_rate_t(0.0, "nitisol"), 0.0)
        self.assertEqual(ethio.lime_rate_t(-1.0, "nitisol"), 0.0)


class TestScheduleAndPlan(unittest.TestCase):
    def test_within_ceiling_is_single_season(self):
        schedule = ethio.lime_application_schedule(3.2)
        self.assertEqual(schedule, [{"season": 1, "t_ha": 3.2}])

    def test_heavy_dose_splits_across_seasons(self):
        schedule = ethio.lime_application_schedule(12.2)
        self.assertEqual(
            schedule,
            [{"season": 1, "t_ha": 5.0},
             {"season": 2, "t_ha": 5.0},
             {"season": 3, "t_ha": 2.2}],
        )
        self.assertAlmostEqual(sum(e["t_ha"] for e in schedule), 12.2)

    def test_non_positive_total_is_empty(self):
        self.assertEqual(ethio.lime_application_schedule(0.0), [])
        self.assertEqual(ethio.lime_application_schedule(-2.0), [])

    def test_plan_block_fields(self):
        plan = ethio.lime_plan(1.5, "vertisol")
        self.assertEqual(plan["soil_type"], "vertisol")
        self.assertEqual(plan["lrf"], 5.5)
        self.assertEqual(plan["total_t_ha"], 8.25)
        self.assertTrue(plan["split_required"])
        self.assertEqual(
            plan["schedule"],
            [{"season": 1, "t_ha": 5.0}, {"season": 2, "t_ha": 3.25}])
        self.assertEqual(plan["lime_type"], "calcitic")
        self.assertEqual(plan["ecce_pct"], 90.0)

    def test_plan_unknown_soil_type_is_none(self):
        self.assertIsNone(ethio.lime_plan(1.0, "loam"))


class TestEngineIntegration(unittest.TestCase):
    def test_analysis_carries_canonical_soil_type(self):
        items = analyze_soil(80, 90, 120, 4.8, soil_type="Nitisols",
                             lime_type="dolomitic")
        for item in items:
            self.assertEqual(item["soil_type"], "nitisol")
            self.assertEqual(item["lime_type"], "dolomitic")

    def test_unknown_soil_type_falls_back_to_legacy(self):
        items = analyze_soil(80, 90, 120, 4.8, soil_type="loam")
        for item in items:
            self.assertIsNone(item["soil_type"])
        ph = _rec(items)["pH"]
        # Generic ph band 6.5-7.5 -> deficit 1.7 -> legacy 6.8 t/ha.
        self.assertIn("6.8 t/ha", ph["solution"])
        self.assertNotIn("Ethiopian LRF", ph["solution"])

    def test_solution_uses_lrf_rate_and_schedule(self):
        analysis = analyze_soil(80, 90, 120, 4.8, soil_type="nitisol")
        text = _rec(analysis)["pH"]["solution"]
        # 1.7 deficit x 4.2 = 7.14 t/ha, split 5.0 + 2.14.
        self.assertIn("7.14 t/ha", text)
        self.assertIn("Ethiopian LRF for Nitisols", text)
        self.assertIn("5 t/ha (Season 1) + 2.14 t/ha (Season 2)", text)

    def test_amharic_solution_has_lrf_note_and_geez(self):
        analysis = analyze_soil(80, 90, 120, 4.8, soil_type="vertisol",
                                lime_type="dolomitic")
        text = _rec(analysis, lang="am")["pH"]["solution"]
        self.assertRegex(text, r"[\u1200-\u137F]")
        self.assertIn("የኢትዮጵያ ሎሚ መጠን", text)
        # 1.7 x 5.5 x 0.84 = 7.854 -> 7.85 t/ha.
        self.assertIn("7.85", text)

    def test_no_note_when_ph_adequate(self):
        analysis = analyze_soil(80, 90, 120, 7.0, soil_type="nitisol")
        text = _rec(analysis)["pH"]["solution"]
        self.assertNotIn("Ethiopian LRF", text)

    def test_legacy_path_byte_identical(self):
        legacy = _rec(analyze_soil(80, 90, 120, 4.8))["pH"]["solution"]
        explicit_generic = _rec(
            analyze_soil(80, 90, 120, 4.8, soil_type=None))["pH"]["solution"]
        self.assertEqual(legacy, explicit_generic)


class TestCostIntegration(unittest.TestCase):
    def test_priced_line_equals_report_text(self):
        analysis = analyze_soil(**ACIDIC, soil_type="nitisol")
        text = _rec(analysis)["pH"]["solution"]
        est = fertilizer_cost.estimate(**ACIDIC, soil_type="nitisol")
        line = next(l for l in est["lines"] if l["col"] == "ph")
        match = re.search(r"lime \(CaCO3\) at roughly ([\d.]+) t/ha", text)
        self.assertEqual(line["amount"], float(match.group(1)))
        self.assertEqual(line["amount"], 7.14)

    def test_total_program_not_capped_by_ceiling(self):
        # Vertisol: 1.7 x 5.5 = 9.35 t/ha total (above the 5.0 ceiling the
        # cost line still prices the WHOLE program; only seasons split).
        est = fertilizer_cost.estimate(**ACIDIC, soil_type="vertisol")
        line = next(l for l in est["lines"] if l["col"] == "ph")
        self.assertEqual(line["amount"], 9.35)
        self.assertTrue(line["purchase"])

    def test_dolomitic_adjustment_in_cost(self):
        est = fertilizer_cost.estimate(
            **ACIDIC, soil_type="vertisol", lime_type="dolomitic")
        line = next(l for l in est["lines"] if l["col"] == "ph")
        self.assertEqual(line["amount"], round(1.7 * 5.5 * 0.84, 2))

    def test_no_soil_type_keeps_legacy_amount(self):
        est = fertilizer_cost.estimate(**ACIDIC)
        line = next(l for l in est["lines"] if l["col"] == "ph")
        self.assertEqual(line["amount"], 6.8)  # legacy 1.7/0.5*2


class TestPredictApi(unittest.TestCase):
    def test_unknown_soil_type_rejected(self):
        resp = client.post(
            "/api/predict", json={**ACIDIC, "soil_type": "loam"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("soil type", resp.json()["detail"].lower())
        self.assertIn("nitisol", resp.json()["detail"])

    def test_invalid_lime_type_rejected(self):
        resp = client.post(
            "/api/predict",
            json={**ACIDIC, "soil_type": "nitisol", "lime_type": "pearl"})
        self.assertEqual(resp.status_code, 422)

    def _predict(self, **extra):
        resp = client.post("/api/predict", json={**ACIDIC, **extra})
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_lime_plan_block_additive(self):
        body = self._predict(soil_type="nitisol")
        plan = body["lime_plan"]
        self.assertEqual(plan["soil_type"], "nitisol")
        self.assertEqual(plan["lrf"], 4.2)
        self.assertEqual(plan["total_t_ha"], 7.14)
        self.assertTrue(plan["split_required"])
        self.assertEqual(plan["schedule"][0], {"season": 1, "t_ha": 5.0})
        self.assertEqual(plan["schedule"][1], {"season": 2, "t_ha": 2.14})
        # Solution text matches the plan total.
        ph = next(r for r in body["recommendations"] if r["element"] == "pH")
        self.assertIn("7.14 t/ha", ph["solution"])

    def test_no_lime_plan_without_soil_type(self):
        body = self._predict()
        self.assertNotIn("lime_plan", body)
        ph = next(r for r in body["recommendations"] if r["element"] == "pH")
        self.assertIn("6.8 t/ha", ph["solution"])  # legacy amount

    def test_no_lime_plan_when_ph_adequate(self):
        body = self._predict(ph=7.0, soil_type="nitisol")
        self.assertNotIn("lime_plan", body)

    def test_display_name_spelling_accepted(self):
        body = self._predict(soil_type="Nitisols")
        self.assertEqual(body["lime_plan"]["soil_type"], "nitisol")

    def test_dolomitic_plan(self):
        body = self._predict(soil_type="nitisol", lime_type="dolomitic")
        self.assertEqual(body["lime_plan"]["lime_type"], "dolomitic")
        self.assertEqual(body["lime_plan"]["total_t_ha"], 6.0)  # 7.14*0.84


class TestCostEstimateApi(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_db_path = config.DB_PATH
        config.DB_PATH = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def _saved_report(self):
        resp = client.post("/api/reports", json={
            "plot": "Lime Plot", "N": 80, "P": 90, "K": 120, "ph": 4.8,
            "fertility_status": "Poor", "recommendations": [],
        })
        self.assertEqual(resp.status_code, 200)
        return resp.json()["id"]

    def test_query_params_switch_to_lrf(self):
        rid = self._saved_report()
        legacy = client.get(f"/api/reports/{rid}/cost-estimate").json()
        legacy_line = next(l for l in legacy["lines"] if l["col"] == "ph")
        self.assertEqual(legacy_line["amount"], 6.8)

        lrf = client.get(
            f"/api/reports/{rid}/cost-estimate",
            params={"soil_type": "vertisol"}).json()
        lrf_line = next(l for l in lrf["lines"] if l["col"] == "ph")
        self.assertEqual(lrf_line["amount"], 9.35)

    def test_dolomitic_query_param(self):
        rid = self._saved_report()
        est = client.get(
            f"/api/reports/{rid}/cost-estimate",
            params={"soil_type": "nitisol", "lime_type": "dolomitic"}).json()
        line = next(l for l in est["lines"] if l["col"] == "ph")
        self.assertEqual(line["amount"], 6.0)

    def test_unknown_soil_type_query_400(self):
        rid = self._saved_report()
        resp = client.get(
            f"/api/reports/{rid}/cost-estimate",
            params={"soil_type": "loam"})
        self.assertEqual(resp.status_code, 400)


class TestBuildSoilRowSoilType(unittest.TestCase):
    def test_v6_schema_aligns_soil_type_dummies(self):
        import pandas as pd
        from ML.engine import build_soil_row

        class _FakeV6:
            feature_cols_ = (
                config.FEATURE_COLS
                + [f"zone_{z}" for z in ethio.list_zones()]
                + [f"soil_type_{s}" for s in ethio.list_soil_types()]
            )

        row = pd.DataFrame([{"N": 50, "P": 20, "K": 120, "ph": 6.5,
                             "zone": None, "soil_type": "nitisol"}])
        aligned = build_soil_row(row, _FakeV6())
        self.assertEqual(list(aligned.columns), _FakeV6.feature_cols_)
        self.assertEqual(aligned["soil_type_nitisol"].iloc[0], 1)
        self.assertEqual(aligned["soil_type_vertisol"].iloc[0], 0)
        # Missing soil type stays all-zero (unseen-zone average).
        row2 = pd.DataFrame([{"N": 50, "P": 20, "K": 120, "ph": 6.5,
                              "zone": None, "soil_type": None}])
        aligned2 = build_soil_row(row2, _FakeV6())
        soil_cols = [c for c in aligned2.columns if c.startswith("soil_type_")]
        self.assertEqual(int(aligned2[soil_cols].sum(axis=1).iloc[0]), 0)

    def test_legacy_model_schema_drops_soil_type(self):
        import pandas as pd
        from ML.engine import build_soil_row

        class _FakeLegacy:
            feature_cols_ = config.FEATURE_COLS

        row = pd.DataFrame([{"N": 50, "P": 20, "K": 120, "ph": 6.5,
                             "zone": None, "soil_type": "nitisol"}])
        aligned = build_soil_row(row, _FakeLegacy())
        self.assertEqual(list(aligned.columns), config.FEATURE_COLS)


class TestConfigV6Profile(unittest.TestCase):
    def test_v6_profile_maps_to_v6_artifact(self):
        import importlib
        prev = config.SOIL_MODEL_PROFILE
        try:
            config.SOIL_MODEL_PROFILE = "agroecology_v6"
            self.assertEqual(
                config.soil_model_path(), config.SOIL_MODEL_ETHIO_V6_PKL)
            self.assertTrue(str(config.SOIL_MODEL_ETHIO_V6_PKL).endswith(
                "soil_analysis_ethio_v6.pkl"))
            config.SOIL_MODEL_PROFILE = "agroecology"
            self.assertEqual(
                config.soil_model_path(), config.SOIL_MODEL_ETHIO_PKL)
        finally:
            config.SOIL_MODEL_PROFILE = prev


if __name__ == "__main__":
    unittest.main()
