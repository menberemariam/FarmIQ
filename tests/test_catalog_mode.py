"""Phase 2: FERT_PRODUCT_CATALOG product selection (flag on and off).

Pins the additive behavior: flag off (default) the engine, cost lines and
API responses stay on the legacy DAP / potassium-sulfate program even when
sulfur/boron/region inputs are supplied; flag on the EthioSIS decision
rules select NPS/NPSB and region blends, and the S/B verdicts, product
trace and application guide surface as response-level extras only.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

# Point everything at a throwaway DB before importing the API.
_tmpdir = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmpdir.name) / "test_catalog_mode.db"

import db  # noqa: E402
import fertilizer_catalog  # noqa: E402
import fertilizer_cost  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import (  # noqa: E402
    _fertilizer_amount,
    analyze_soil,
    application_schedule,
    build_prompt,
    micronutrient_check,
    product_for_rec,
    product_selection_block,
    recommend_soil,
)

db.init_db()
client = TestClient(api.app)

ON = {"FERT_PRODUCT_CATALOG": "on"}
OFF = {"FERT_PRODUCT_CATALOG": "off"}

# P deficient by 20 kg/ha (58 against the 78-117 band), K deficient
# by 18.2 kg/ha K2O, N deficient.
DEFICIENT_P = {"N": 80.0, "P": 58.0, "K": 120.0, "ph": 6.8}
DEFICIENT_K = {"N": 80.0, "P": 90.0, "K": 60.0, "ph": 6.8}
DEFICIENT_N = {"N": 20.0, "P": 90.0, "K": 120.0, "ph": 6.8}
ALL_DEFICIENT = {"N": 20.0, "P": 10.0, "K": 60.0, "ph": 6.8}


class TestResolveSelection(unittest.TestCase):
    def test_flag_off_returns_legacy_even_with_all_inputs(self):
        with mock.patch.dict(os.environ, OFF):
            sel = fertilizer_catalog.resolve_selection(
                crop="potato", sulfur_ppm=10.0, boron_ppm=0.1,
                region="Gojjam",
            )
        self.assertEqual(sel, {"N": "urea", "P": "dap", "K": "potassium_sulfate"})

    def test_flag_on_defaults_to_nps_and_potassium_sulfate(self):
        with mock.patch.dict(os.environ, ON):
            sel = fertilizer_catalog.resolve_selection()
        self.assertEqual(sel, {"N": "urea", "P": "nps", "K": "potassium_sulfate"})

    def test_flag_on_boron_deficient_selects_npsb(self):
        with mock.patch.dict(os.environ, ON):
            self.assertEqual(
                fertilizer_catalog.resolve_selection(boron_ppm=0.3)["P"], "npsb")
            # Boundary: exactly 0.5 ppm is sufficient -> NPS.
            self.assertEqual(
                fertilizer_catalog.resolve_selection(boron_ppm=0.5)["P"], "nps")

    def test_flag_on_boron_toxic_never_npsb(self):
        with mock.patch.dict(os.environ, ON):
            self.assertEqual(
                fertilizer_catalog.resolve_selection(boron_ppm=2.5)["P"], "nps")

    def test_flag_on_region_targets_blend(self):
        with mock.patch.dict(os.environ, ON):
            self.assertEqual(
                fertilizer_catalog.resolve_selection(region="Gojjam")["K"],
                "blended")
            self.assertEqual(
                fertilizer_catalog.resolve_selection(region="Western Shewa")["K"],
                "blended")
            self.assertEqual(
                fertilizer_catalog.resolve_selection(region="Addis Ababa")["K"],
                "potassium_sulfate")

    def test_flag_on_crop_targets_blend(self):
        with mock.patch.dict(os.environ, ON):
            for crop in ("potato", "cassava", "enset", "avocado"):
                self.assertEqual(
                    fertilizer_catalog.resolve_selection(crop=crop)["K"],
                    "blended", crop)
            self.assertEqual(
                fertilizer_catalog.resolve_selection(crop="maize")["K"],
                "potassium_sulfate")

    def test_catalog_mode_flag_values(self):
        for raw, expected in (("off", False), ("0", False), ("false", False),
                              ("no", False), ("", False), ("on", True),
                              ("1", True), ("true", True), ("YES", True)):
            with mock.patch.dict(os.environ, {"FERT_PRODUCT_CATALOG": raw}):
                self.assertEqual(fertilizer_catalog.catalog_mode_enabled(),
                                 expected, raw)


class TestNutrientContent(unittest.TestCase):
    def test_catalog_products(self):
        self.assertAlmostEqual(
            fertilizer_catalog.nutrient_content("P", "nps"), 0.38)
        self.assertAlmostEqual(
            fertilizer_catalog.nutrient_content("P", "npsb"), 0.369)
        self.assertAlmostEqual(
            fertilizer_catalog.nutrient_content("K", "blended"), 0.1405)

    def test_legacy_products_keep_engine_contents(self):
        self.assertAlmostEqual(
            fertilizer_catalog.nutrient_content("N", "urea"), 0.46)
        self.assertAlmostEqual(
            fertilizer_catalog.nutrient_content("P", "dap"), 0.46)
        self.assertAlmostEqual(
            fertilizer_catalog.nutrient_content("K", "potassium_sulfate"), 0.50)
        # Unknown product ids fall back to the engine defaults.
        self.assertAlmostEqual(
            fertilizer_catalog.nutrient_content("P", "nonexistent"), 0.46)


class TestEngineCatalogText(unittest.TestCase):
    def setUp(self):
        self.analysis = analyze_soil(**DEFICIENT_P)

    def test_flag_off_rec_names_dap(self):
        with mock.patch.dict(os.environ, OFF):
            recs = recommend_soil(self.analysis)
        p = next(r for r in recs if r["element"] == "Phosphorus")
        self.assertIn("DAP", p["solution"])
        self.assertNotIn("NPS ", p["solution"])

    def test_flag_on_rec_names_nps_with_matching_amount(self):
        with mock.patch.dict(os.environ, ON):
            recs = recommend_soil(self.analysis)
        p = next(r for r in recs if r["element"] == "Phosphorus")
        amount = _fertilizer_amount(20.0, 0.38)
        self.assertIn("NPS", p["solution"])
        self.assertIn(f"{amount} kg/ha", p["solution"])
        self.assertNotIn("DAP", p["solution"])

    def test_flag_on_npsb_when_boron_deficient(self):
        with mock.patch.dict(os.environ, ON):
            selection = fertilizer_catalog.resolve_selection(boron_ppm=0.2)
            recs = recommend_soil(self.analysis, selection=selection)
        p = next(r for r in recs if r["element"] == "Phosphorus")
        self.assertIn("NPSB", p["solution"])
        amount = _fertilizer_amount(20.0, 0.369)
        self.assertIn(f"{amount} kg/ha", p["solution"])

    def test_flag_on_blend_when_crop_targets_k(self):
        analysis = analyze_soil(**DEFICIENT_K, crop="potato")
        with mock.patch.dict(os.environ, ON):
            selection = fertilizer_catalog.resolve_selection(crop="potato")
            recs = recommend_soil(analysis, selection=selection)
        k = next(r for r in recs if r["element"] == "Potassium")
        deficit = next(i for i in analysis if i["col"] == "K")["deficit"]
        amount = _fertilizer_amount(deficit, 0.1405)
        self.assertIn("NPSKB", k["solution"])
        self.assertIn(f"{amount} kg/ha", k["solution"])

    def test_amharic_catalog_and_legacy_text(self):
        with mock.patch.dict(os.environ, OFF):
            off = recommend_soil(self.analysis, lang="am")
        with mock.patch.dict(os.environ, ON):
            on = recommend_soil(self.analysis, lang="am")
        p_off = next(r for r in off if r["element"] == "Phosphorus")["solution"]
        p_on = next(r for r in on if r["element"] == "Phosphorus")["solution"]
        self.assertIn("ዲኤፒ", p_off)
        self.assertIn("ኤንፒኤስ", p_on)

    def test_prompt_contents_line_legacy_and_catalog(self):
        legacy = build_prompt(self.analysis)
        self.assertIn("DAP is 46% P2O5", legacy)
        with mock.patch.dict(os.environ, ON):
            selection = fertilizer_catalog.resolve_selection()
        catalog = build_prompt(self.analysis, selection=selection)
        self.assertIn("NPS is 38% P2O5", catalog)
        self.assertNotIn("DAP is 46% P2O5", catalog)
        blended = build_prompt(
            self.analysis,
            selection={"N": "urea", "P": "npsb", "K": "blended"},
        )
        self.assertIn("NPSB is 36.9% P2O5", blended)
        self.assertIn("NPSKB blend is 14.1% K2O", blended)


class TestMicronutrientCheck(unittest.TestCase):
    def test_absent_inputs_return_none(self):
        self.assertIsNone(micronutrient_check())
        self.assertIsNone(micronutrient_check(None, None))

    def test_sulfur_deficient_and_adequate(self):
        out = micronutrient_check(sulfur_ppm=15.0)
        sulfur = out["sulfur"]
        self.assertEqual(sulfur["status"], "deficient")
        self.assertEqual(sulfur["threshold"], 20.0)
        self.assertIn("15.0", sulfur["message"])
        self.assertNotIn("boron", out)
        out = micronutrient_check(sulfur_ppm=25.0)
        self.assertEqual(out["sulfur"]["status"], "adequate")

    def test_boron_statuses(self):
        self.assertEqual(micronutrient_check(boron_ppm=0.3)["boron"]["status"],
                         "deficient")
        self.assertEqual(micronutrient_check(boron_ppm=1.0)["boron"]["status"],
                         "adequate")
        excess = micronutrient_check(boron_ppm=2.5)["boron"]
        self.assertEqual(excess["status"], "excess")
        self.assertEqual(excess["toxic_above"], 2.0)

    def test_amharic_messages(self):
        out = micronutrient_check(sulfur_ppm=15.0, boron_ppm=0.3, lang="am")
        self.assertIn("ፒ.ፒ.ኤም", out["sulfur"]["message"])
        self.assertIn("ቦሮን", out["boron"]["message"])


class TestProductSelectionBlock(unittest.TestCase):
    def test_flag_off_block_explains_legacy(self):
        with mock.patch.dict(os.environ, OFF):
            sel = fertilizer_catalog.resolve_selection()
            block = product_selection_block(sel, sulfur_ppm=15.0)
        self.assertFalse(block["catalog_mode"])
        self.assertIn("FERT_PRODUCT_CATALOG", block["reasons"][0])
        self.assertEqual(block["products"]["P"]["id"], "dap")

    def test_flag_on_block_traces_npsb_and_blend(self):
        with mock.patch.dict(os.environ, ON):
            sel = fertilizer_catalog.resolve_selection(
                crop="potato", boron_ppm=0.3)
            block = product_selection_block(
                sel, crop="potato", boron_ppm=0.3, region="Gojjam")
        self.assertTrue(block["catalog_mode"])
        self.assertEqual(block["products"]["P"]["id"], "npsb")
        self.assertEqual(block["products"]["K"]["id"], "blended")
        joined = " ".join(block["reasons"])
        self.assertIn("0.3", joined)
        self.assertIn("Gojjam", joined)

    def test_flag_on_amharic_reasons(self):
        with mock.patch.dict(os.environ, ON):
            sel = fertilizer_catalog.resolve_selection()
            block = product_selection_block(sel, lang="am")
        self.assertIn("ኤንፒኤስ", block["reasons"][0])
        self.assertEqual(block["products"]["P"]["label"],
                         "NPS (19% N, 38% P2O5, 7% S)")


class TestApplicationSchedule(unittest.TestCase):
    def test_urea_split(self):
        schedule = application_schedule("urea")
        self.assertEqual(schedule["timing"], "split")
        self.assertEqual(len(schedule["splits"]), 2)
        self.assertEqual(schedule["splits"][0]["share"], "1/3")
        self.assertEqual(schedule["splits"][1]["share"], "2/3")
        self.assertTrue(schedule["restrictions"])

    def test_basal_products(self):
        for product in ("nps", "npsb", "dap", "blended", "potassium_sulfate"):
            schedule = application_schedule(product)
            self.assertEqual(schedule["timing"], "basal", product)
            self.assertTrue(schedule["splits"], product)

    def test_maintenance_and_unknown(self):
        schedule = application_schedule(None, lang="en")
        self.assertEqual(schedule["timing"], "maintenance")
        self.assertIn("Maintenance", schedule["note"])
        amharic = application_schedule(None, lang="am")
        self.assertIn("ጥገና", amharic["note"])
        self.assertIsNone(application_schedule("unobtainium"))

    def test_amharic_urea(self):
        schedule = application_schedule("urea", lang="am")
        self.assertIn("በመዝራት ጊዜ", schedule["splits"][0]["stage"])

    def test_product_for_rec(self):
        selection = {"N": "urea", "P": "nps", "K": "potassium_sulfate"}
        self.assertEqual(product_for_rec("ph", "deficient"), "lime")
        self.assertEqual(product_for_rec("ph", "ample"), "sulfur")
        self.assertIsNone(product_for_rec("P", "adequate", selection))
        self.assertEqual(product_for_rec("P", "deficient", selection), "nps")


class TestCostCatalogMode(unittest.TestCase):
    def _line(self, est, element):
        return next(l for l in est["lines"] if l["element"] == element)

    def test_flag_off_cost_ignores_s_b_region(self):
        with mock.patch.dict(os.environ, OFF):
            est = fertilizer_cost.estimate(
                **DEFICIENT_P, sulfur_ppm=10.0, boron_ppm=0.1, region="Gojjam")
        p = self._line(est, "Phosphorus")
        self.assertEqual(p["product"], "dap")
        self.assertEqual(p["amount"], _fertilizer_amount(20.0, 0.46))
        self.assertEqual(p["price_source"], "db")

    def test_flag_on_p_line_uses_nps_with_legacy_option(self):
        with mock.patch.dict(os.environ, ON):
            est = fertilizer_cost.estimate(**DEFICIENT_P)
        p = self._line(est, "Phosphorus")
        self.assertEqual(p["product"], "nps")
        self.assertEqual(p["product_label"], "NPS (19% N, 38% P2O5, 7% S)")
        self.assertEqual(p["amount"], _fertilizer_amount(20.0, 0.38))
        self.assertEqual(p["price_source"], "catalog")
        self.assertGreater(p["price_etb_per_kg"], 0.0)
        options = {o["product"]: o for o in p["product_options"]}
        self.assertTrue(options["nps"]["primary"])
        self.assertEqual(options["nps"]["amount"], p["amount"])
        self.assertFalse(options["dap"]["primary"])
        self.assertEqual(options["dap"]["price_source"], "db")
        self.assertIn("npsb", options)

    def test_flag_on_boron_deficient_costs_npsb(self):
        with mock.patch.dict(os.environ, ON):
            est = fertilizer_cost.estimate(**DEFICIENT_P, boron_ppm=0.2)
        p = self._line(est, "Phosphorus")
        self.assertEqual(p["product"], "npsb")
        self.assertEqual(p["amount"], _fertilizer_amount(20.0, 0.369))
        self.assertEqual(p["price_source"], "catalog")

    def test_flag_on_region_costs_blend(self):
        with mock.patch.dict(os.environ, ON):
            est = fertilizer_cost.estimate(**DEFICIENT_K, region="Gojjam")
        k = self._line(est, "Potassium")
        self.assertEqual(k["product"], "blended")
        self.assertEqual(k["amount"], _fertilizer_amount(18.2, 0.1405))
        options = {o["product"]: o for o in k["product_options"]}
        self.assertFalse(options["potassium_sulfate"]["primary"])
        self.assertEqual(options["potassium_sulfate"]["price_source"], "db")

    def test_flag_on_n_still_costs_db_urea(self):
        with mock.patch.dict(os.environ, ON):
            est = fertilizer_cost.estimate(**DEFICIENT_N)
        n = self._line(est, "Nitrogen")
        self.assertEqual(n["product"], "urea")
        self.assertEqual(n["price_source"], "db")

    def test_cost_amount_matches_report_text(self):
        analysis = analyze_soil(**DEFICIENT_P)
        with mock.patch.dict(os.environ, ON):
            selection = fertilizer_catalog.resolve_selection()
            recs = recommend_soil(analysis, selection=selection)
            est = fertilizer_cost.estimate(**DEFICIENT_P)
        p_rec = next(r for r in recs if r["element"] == "Phosphorus")
        p_line = self._line(est, "Phosphorus")
        self.assertIn(f"{p_line['amount']} kg/ha", p_rec["solution"])


class TestApiCatalogMode(unittest.TestCase):
    def test_default_request_has_no_phase2_blocks(self):
        resp = client.post("/api/predict", json=DEFICIENT_P)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertNotIn("micronutrients", body)
        self.assertNotIn("product_selection", body)
        for rec in body["recommendations"]:
            self.assertEqual(
                set(rec),
                {"element", "status", "deficit", "value", "ideal_range",
                 "solution", "source", "lang"},
            )
            self.assertNotIn("application", rec)

    def test_sulfur_boron_add_response_blocks(self):
        resp = client.post(
            "/api/predict",
            json={**DEFICIENT_P, "sulfur_ppm": 15.0, "boron_ppm": 0.3},
        )
        body = resp.json()
        self.assertEqual(body["micronutrients"]["sulfur"]["status"], "deficient")
        self.assertEqual(body["micronutrients"]["boron"]["status"], "deficient")
        selection = body["product_selection"]
        self.assertFalse(selection["catalog_mode"])
        self.assertEqual(selection["products"]["P"]["id"], "dap")
        self.assertEqual(selection["products"]["K"]["id"], "potassium_sulfate")

    def test_include_application_guide_adds_key_only(self):
        resp = client.post(
            "/api/predict", json={**DEFICIENT_P, "include_application_guide": True})
        body = resp.json()
        p_rec = next(r for r in body["recommendations"]
                     if r["element"] == "Phosphorus")
        self.assertEqual(p_rec["application"]["product"], "dap")
        self.assertEqual(p_rec["application"]["timing"], "basal")
        k_rec = next(r for r in body["recommendations"]
                     if r["element"] == "Potassium")
        # K is adequate here -> maintenance entry, not a product.
        self.assertIsNone(k_rec["application"]["product"])
        self.assertIn("Maintenance", k_rec["application"]["note"])

        urea_resp = client.post(
            "/api/predict",
            json={**DEFICIENT_N, "include_application_guide": True})
        n_rec = next(r for r in urea_resp.json()["recommendations"]
                     if r["element"] == "Nitrogen")
        self.assertEqual(n_rec["application"]["product"], "urea")
        self.assertEqual(len(n_rec["application"]["splits"]), 2)

    def test_flag_on_predict_names_nps_and_traces_selection(self):
        with mock.patch.dict(os.environ, ON):
            resp = client.post(
                "/api/predict",
                json={**DEFICIENT_P, "sulfur_ppm": 25.0, "boron_ppm": 1.0,
                      "region": "Gojjam", "include_application_guide": True},
            )
        body = resp.json()
        p_rec = next(r for r in body["recommendations"]
                     if r["element"] == "Phosphorus")
        self.assertIn("NPS", p_rec["solution"])
        self.assertNotIn("DAP", p_rec["solution"])
        self.assertEqual(p_rec["application"]["product"], "nps")
        selection = body["product_selection"]
        self.assertTrue(selection["catalog_mode"])
        self.assertEqual(selection["products"]["P"]["id"], "nps")
        self.assertEqual(selection["products"]["K"]["id"], "blended")
        self.assertEqual(body["micronutrients"]["sulfur"]["status"], "adequate")
        self.assertEqual(body["micronutrients"]["boron"]["status"], "adequate")

    def test_flag_on_boron_deficient_api_selects_npsb(self):
        with mock.patch.dict(os.environ, ON):
            resp = client.post(
                "/api/predict", json={**DEFICIENT_P, "boron_ppm": 0.2})
        body = resp.json()
        self.assertEqual(body["product_selection"]["products"]["P"]["id"], "npsb")

    def test_amharic_phase2_messages(self):
        resp = client.post(
            "/api/predict",
            json={**DEFICIENT_P, "sulfur_ppm": 15.0, "lang": "am"},
        )
        body = resp.json()
        self.assertIn("ፒ.ፒ.ኤም", body["micronutrients"]["sulfur"]["message"])


class TestReportPersistence(unittest.TestCase):
    def test_s_b_region_round_trip_and_cost(self):
        resp = client.post(
            "/api/reports",
            json={
                "plot": "catalog-mode-plot",
                **ALL_DEFICIENT,
                "fertility_status": "Moderate",
                "recommendations": [],
                "sulfur_ppm": 15.0,
                "boron_ppm": 1.0,
                "region": "Gojjam",
            },
        )
        self.assertEqual(resp.status_code, 200)
        report_id = resp.json()["id"]

        report = db.get_report(report_id)
        self.assertEqual(report["sulfur_ppm"], 15.0)
        self.assertEqual(report["boron_ppm"], 1.0)
        self.assertEqual(report["region"], "Gojjam")

        with mock.patch.dict(os.environ, ON):
            cost = client.get(
                f"/api/reports/{report_id}/cost-estimate").json()
        n = next(l for l in cost["lines"] if l["element"] == "Nitrogen")
        p = next(l for l in cost["lines"] if l["element"] == "Phosphorus")
        k = next(l for l in cost["lines"] if l["element"] == "Potassium")
        self.assertEqual(n["product"], "urea")       # always urea
        self.assertEqual(p["product"], "nps")        # B=1.0 ppm sufficient
        self.assertEqual(k["product"], "blended")    # region Gojjam
        self.assertEqual(k["amount"], _fertilizer_amount(
            next(i for i in analyze_soil(**ALL_DEFICIENT) if i["col"] == "K")
            ["deficit"], 0.1405))


if __name__ == "__main__":
    unittest.main()
