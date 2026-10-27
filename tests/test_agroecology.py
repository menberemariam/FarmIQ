"""Tests for the agroecological-zone integration (Dataset/agroecological_zones.json).

Pins the zone-aware analysis contract:
  * zone bands drive N/P/K/pH analysis and replace generic AND crop bands;
  * no zone supplied -> behavior is byte-identical to the legacy path;
  * unknown zones are rejected (400) on /api/predict and /api/reports;
  * zone notes (lime-first, split N) append to the matching recommendations;
  * saved reports keep their zone so cost estimates recompute with it;
  * the additive `agroecology` response block appears only when requested.

Phase 3 overrides (machine-readable per-zone rules):
  * P-efficiency — acid highlands divide P deficits by the fixation factor;
  * K bypass — Vertisol K at/above the threshold is never a purchase;
  * NPSZnB — Rift Valley pH above the flag switches the basal blend;
  * salinity pause — arid ECe above the threshold suspends fertilizer
    (gypsum/leaching text) and zeroes cost lines; ESP adds sodicity.
"""

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
from ML.engine import (  # noqa: E402
    _apply_zone_overrides,
    analyze_soil,
    get_bands,
    recommend_soil,
)
import api  # noqa: E402

client = TestClient(api.app)


class TestZoneBands(unittest.TestCase):
    def test_zone_bands_replace_generic_and_crop_bands(self):
        zone_bands = get_bands(crop="rice", zone="highland_nitisols")
        self.assertEqual(zone_bands["N"], (130.0, 260.0))
        self.assertEqual(zone_bands["P"], (39.0, 78.0))
        # crop bands would be rice's (76-84.4 etc.) — the zone wins.
        self.assertNotEqual(zone_bands, get_bands(crop="rice"))

    def test_no_zone_keeps_crop_bands(self):
        self.assertEqual(get_bands(crop="rice"), get_bands(crop="rice", zone=None))
        self.assertEqual(list(get_bands(crop="rice")["N"]), [76.0, 84.4])

    def test_no_zone_no_crop_keeps_config_ranges(self):
        self.assertEqual(get_bands(), config.IDEAL_RANGES)
        self.assertEqual(get_bands(zone=None), config.IDEAL_RANGES)

    def test_unknown_zone_falls_through_to_legacy(self):
        # get_bands is forgiving (silent fall-through) — the API is the guard.
        self.assertEqual(get_bands(crop="rice", zone="cambisol"),
                         get_bands(crop="rice"))
        self.assertEqual(get_bands(zone=""), config.IDEAL_RANGES)

    def test_analysis_items_carry_zone(self):
        items = analyze_soil(150, 40, 300, 5.2, crop="rice",
                             zone="highland_nitisols")
        for item in items:
            self.assertEqual(item["zone"], "highland_nitisols")
            self.assertEqual(item["crop"], "rice")
        legacy = analyze_soil(150, 40, 300, 5.2, crop="rice")
        self.assertIsNone(legacy[0]["zone"])

    def test_zone_deficits_use_zone_bands(self):
        # pH 5.2 vs Nitisol ideal 5.5 -> deficit 0.3 (not the rice/generic gap).
        items = {i["col"]: i for i in analyze_soil(
            150, 40, 300, 5.2, zone="highland_nitisols")}
        self.assertAlmostEqual(items["ph"]["deficit"], 0.3)
        self.assertEqual(items["ph"]["status"], "deficient")
        # K 300 is inside the Nitisol band but far above the generic one.
        self.assertEqual(items["K"]["status"], "adequate")
        self.assertEqual(items["K"]["deficit"], 0.0)


class TestZoneNotes(unittest.TestCase):
    def test_nitisol_lime_first_note_on_acidic_ph(self):
        recs = recommend_soil(
            analyze_soil(150, 40, 300, 5.2, zone="highland_nitisols"))
        ph = next(r for r in recs if r["element"] == "pH")
        self.assertIn("lime", ph["solution"].lower())
        self.assertIn("first", ph["solution"].lower())

    def test_no_lime_first_note_above_threshold(self):
        # pH 6.0 is deficient vs 6.5 but above the 5.5 lime-first threshold.
        recs = recommend_soil(
            analyze_soil(150, 40, 300, 6.0, zone="highland_nitisols"))
        ph = next(r for r in recs if r["element"] == "pH")
        self.assertNotIn("lime (CaCO₃) first", ph["solution"])

    def test_vertisol_split_n_note(self):
        recs = recommend_soil(
            analyze_soil(160, 50, 400, 7.0, zone="highland_vertisols"))
        n = next(r for r in recs if r["element"] == "Nitrogen")
        self.assertIn("Split", n["solution"])
        self.assertIn("3", n["solution"])

    def test_no_zone_no_note(self):
        recs = recommend_soil(analyze_soil(160, 50, 400, 7.0))
        n = next(r for r in recs if r["element"] == "Nitrogen")
        self.assertNotIn("Split", n["solution"])

    def test_zone_note_amharic(self):
        recs = recommend_soil(
            analyze_soil(150, 40, 300, 5.2, zone="highland_nitisols"),
            lang="am")
        ph = next(r for r in recs if r["element"] == "pH")
        self.assertIn("ሎሚ", ph["solution"])
        self.assertRegex(ph["solution"], r"[\u1200-\u137F]")

    def test_note_never_changes_rec_keys(self):
        recs = recommend_soil(
            analyze_soil(150, 40, 300, 5.2, zone="highland_nitisols"))
        for rec in recs:
            self.assertEqual(
                set(rec),
                {"element", "status", "deficit", "solution", "source", "lang"},
            )


class TestZoneCost(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "zone_cost.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def test_zone_estimate_uses_zone_bands(self):
        # N 100: below the Nitisol floor (130) -> deficient; below the
        # generic floor (60)? no — generic would call it adequate.
        legacy = fertilizer_cost.estimate(N=100.0, P=40.0, K=300.0, ph=6.5)
        zoned = fertilizer_cost.estimate(N=100.0, P=40.0, K=300.0, ph=6.5,
                                         zone="highland_nitisols")
        legacy_n = next(l for l in legacy["lines"] if l["element"] == "Nitrogen")
        zoned_n = next(l for l in zoned["lines"] if l["element"] == "Nitrogen")
        self.assertFalse(legacy_n["purchase"])   # 100 >= generic 100 top... 60-100
        self.assertTrue(zoned_n["purchase"])     # 100 < 130 Nitisol floor
        self.assertAlmostEqual(zoned_n["deficit"], 30.0)


class TestPredictEndpoint(unittest.TestCase):
    def test_zone_request_adds_agroecology_block(self):
        resp = client.post(
            "/api/predict",
            json={"N": 150, "P": 40, "K": 300, "ph": 5.2,
                  "agroecological_zone": "highland_nitisols"},
        )
        self.assertEqual(resp.status_code, 200)
        block = resp.json()["agroecology"]
        self.assertEqual(block["zone"], "highland_nitisols")
        self.assertEqual(block["name"], "Highland Nitisols")
        self.assertEqual(block["bands"]["P"], [39.0, 78.0])
        self.assertIn("constraint", block)
        self.assertIn("source", block)

    def test_zone_request_switches_ideal_ranges(self):
        resp = client.post(
            "/api/predict",
            json={"N": 150, "P": 40, "K": 300, "ph": 5.2,
                  "agroecological_zone": "rift_valley", "crop": "rice"},
        )
        ideals = [r["ideal_range"] for r in resp.json()["recommendations"]]
        self.assertEqual(ideals[0], "100-200 kg/ha")   # zone, not rice's 76-84
        self.assertEqual(ideals[3], "6.5-8.0")

    def test_zone_name_localized(self):
        resp = client.post(
            "/api/predict",
            json={"N": 150, "P": 40, "K": 300, "ph": 5.2,
                  "agroecological_zone": "rift_valley", "lang": "am"},
        )
        self.assertEqual(resp.json()["agroecology"]["name"],
                         "የሪፍት ሸለቆ አፈሮች")

    def test_legacy_request_has_no_agroecology_block(self):
        resp = client.post("/api/predict",
                           json={"N": 80, "P": 90, "K": 120, "ph": 6.8})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("agroecology", resp.json())

    def test_unknown_zone_rejected(self):
        resp = client.post(
            "/api/predict",
            json={"N": 80, "P": 90, "K": 120, "ph": 6.8,
                  "agroecological_zone": "cambisol"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cambisol", resp.json()["detail"])

    def test_flexible_zone_spelling_accepted(self):
        resp = client.post(
            "/api/predict",
            json={"N": 80, "P": 90, "K": 120, "ph": 6.8,
                  "agroecological_zone": "Highland Vertisols"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["agroecology"]["zone"],
                         "highland_vertisols")

    def test_zone_notes_in_solutions(self):
        resp = client.post(
            "/api/predict",
            json={"N": 150, "P": 40, "K": 300, "ph": 5.2,
                  "agroecological_zone": "highland_nitisols"},
        )
        ph = next(r for r in resp.json()["recommendations"]
                  if r["element"] == "pH")
        self.assertIn("lime", ph["solution"].lower())


class TestReportRoundTrip(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "zone_report.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def _payload(self, **extra):
        payload = {
            "plot": "Zone Plot", "N": 100, "P": 40, "K": 300, "ph": 6.5,
            "fertility_status": "Moderate", "recommendations": [],
            "agroecological_zone": "highland_nitisols",
        }
        payload.update(extra)
        return payload

    def test_zone_saved_and_cost_recomputes_with_zone(self):
        resp = client.post("/api/reports", json=self._payload())
        self.assertEqual(resp.status_code, 200)
        rid = resp.json()["id"]

        stored = db.get_report(rid)
        self.assertEqual(stored["agroecological_zone"], "highland_nitisols")

        cost = client.get(f"/api/reports/{rid}/cost-estimate").json()
        n_line = next(l for l in cost["lines"] if l["element"] == "Nitrogen")
        self.assertTrue(n_line["purchase"])          # 100 < zone floor 130
        self.assertAlmostEqual(n_line["deficit"], 30.0)

    def test_report_without_zone_stays_legacy(self):
        payload = self._payload()
        payload.pop("agroecological_zone")
        resp = client.post("/api/reports", json=payload)
        rid = resp.json()["id"]
        self.assertIsNone(db.get_report(rid)["agroecological_zone"])
        cost = client.get(f"/api/reports/{rid}/cost-estimate").json()
        n_line = next(l for l in cost["lines"] if l["element"] == "Nitrogen")
        self.assertFalse(n_line["purchase"])         # generic band: 60-100

    def test_unknown_zone_on_save_rejected(self):
        resp = client.post(
            "/api/reports", json=self._payload(agroecological_zone="nope"))
        self.assertEqual(resp.status_code, 400)


def _item(items, col):
    return next(i for i in items if i["col"] == col)


def _rec(recs, element):
    return next(r for r in recs if r["element"] == element)


class TestPEfficiencyOverride(unittest.TestCase):
    """Acid highlands (Nitisols): P deficits are scaled by the fixation factor."""

    def test_strongly_acidic_scales_p_deficit(self):
        # P=30 vs band 39-78 -> raw deficit 9; 9 / 0.4 = 22.5.
        items = analyze_soil(150, 30, 300, 4.8, zone="highland_nitisols")
        p = _item(items, "P")
        self.assertAlmostEqual(p["deficit"], 22.5)
        self.assertTrue(p["p_efficiency"])

    def test_p_above_threshold_is_unscaled(self):
        # pH 5.5 is at the lime-first threshold but NOT below p_efficiency_ph.
        items = analyze_soil(150, 30, 300, 5.5, zone="highland_nitisols")
        p = _item(items, "P")
        self.assertAlmostEqual(p["deficit"], 9.0)
        self.assertIsNone(p.get("p_efficiency"))

    def test_no_zone_never_scales(self):
        # Generic EthioSIS advice band for P is 78-117 kg/ha -> deficit 48,
        # passed through untouched (no fixation factor outside a zone).
        items = analyze_soil(150, 30, 300, 4.5)
        self.assertAlmostEqual(_item(items, "P")["deficit"], 48.0)

    def test_explanation_note_appended(self):
        recs = recommend_soil(
            analyze_soil(150, 30, 300, 4.8, zone="highland_nitisols"))
        self.assertIn("Fe/Al", _rec(recs, "Phosphorus")["solution"])

    def test_note_is_localized(self):
        recs = recommend_soil(
            analyze_soil(150, 30, 300, 4.8, zone="highland_nitisols"),
            lang="am")
        self.assertIn("ፎስፎረስ", _rec(recs, "Phosphorus")["solution"])


class TestKBypassOverride(unittest.TestCase):
    """Vertisols: soil K at/above the threshold is never a purchase."""

    def _k_item(self, value):
        return {
            "element": "Potassium", "col": "K", "value": value,
            "band": (350.0, 700.0), "status": "deficient", "deficit": 50.0,
            "crop": None, "zone": "highland_vertisols",
        }

    def test_bypass_zeroes_deficit_and_explains(self):
        out = _apply_zone_overrides(
            [self._k_item(420.0)], {"k_bypass_above": 400.0})
        k = out[0]
        self.assertEqual(k["status"], "adequate")
        self.assertEqual(k["deficit"], 0.0)
        self.assertTrue(k["k_bypass"])
        recs = recommend_soil(out, zone="highland_vertisols")
        self.assertIn("Vertisol clays", _rec(recs, "Potassium")["solution"])

    def test_below_threshold_keeps_deficit(self):
        out = _apply_zone_overrides(
            [self._k_item(300.0)], {"k_bypass_above": 400.0})
        self.assertEqual(out[0]["deficit"], 50.0)
        self.assertIsNone(out[0].get("k_bypass"))

    def test_real_zone_bands_make_bypass_a_noop(self):
        # With the shipped Vertisol bands (K ideal 350-700), any K >= 400 is
        # already adequate — the bypass is belt-and-braces for band changes.
        items = analyze_soil(200, 50, 420, 7.0, zone="highland_vertisols")
        k = _item(items, "K")
        self.assertEqual(k["status"], "adequate")
        self.assertEqual(k["deficit"], 0.0)


class TestNPSZnBOverride(unittest.TestCase):
    """Rift Valley: pH above the flag switches the basal blend to NPSZnB."""

    def test_high_ph_switches_blend(self):
        recs = recommend_soil(
            analyze_soil(150, 40, 500, 7.8, zone="rift_valley"))
        self.assertIn("NPSZnB", _rec(recs, "Phosphorus")["solution"])

    def test_normal_ph_keeps_plain_nps(self):
        recs = recommend_soil(
            analyze_soil(150, 40, 500, 7.0, zone="rift_valley"))
        self.assertNotIn("NPSZnB", _rec(recs, "Phosphorus")["solution"])

    def test_note_is_localized(self):
        recs = recommend_soil(
            analyze_soil(150, 40, 500, 7.8, zone="rift_valley"), lang="am")
        self.assertIn("NPS + Zn + B", _rec(recs, "Phosphorus")["solution"])


class TestSalinityOverride(unittest.TestCase):
    """Arid zone: ECe above the threshold pauses fertilizer (gypsum/leach)."""

    def test_salient_nutrients_suppressed(self):
        items = analyze_soil(100, 40, 600, 8.0, zone="arid_semi_arid",
                             ec_e=6.0)
        for col in ("N", "P", "K"):
            item = _item(items, col)
            self.assertEqual(item["deficit"], 0.0)
            self.assertTrue(item["suppressed"])
            self.assertEqual(item["ec_e"], 6.0)
        self.assertIsNone(_item(items, "ph").get("suppressed"))

    def test_below_threshold_no_suppression(self):
        items = analyze_soil(80, 30, 600, 8.0, zone="arid_semi_arid", ec_e=2.0)
        self.assertFalse(any(i.get("suppressed") for i in items))

    def test_no_ec_e_no_suppression(self):
        items = analyze_soil(80, 30, 600, 8.0, zone="arid_semi_arid")
        self.assertFalse(any(i.get("suppressed") for i in items))

    def test_gypsum_and_leaching_text(self):
        recs = recommend_soil(
            analyze_soil(100, 40, 600, 8.0, zone="arid_semi_arid", ec_e=6.0),
            zone="arid_semi_arid")
        n = _rec(recs, "Nitrogen")
        self.assertIn("gypsum", n["solution"])
        self.assertIn("leach", n["solution"].lower())
        self.assertEqual(n["deficit"], 0.0)

    def test_sodicity_warning_when_esp_high(self):
        recs = recommend_soil(
            analyze_soil(100, 40, 600, 8.0, zone="arid_semi_arid",
                         ec_e=6.0, esp=18.0),
            zone="arid_semi_arid")
        self.assertIn("ESP 18", _rec(recs, "Nitrogen")["solution"])

    def test_no_sodicity_warning_when_esp_ok(self):
        recs = recommend_soil(
            analyze_soil(100, 40, 600, 8.0, zone="arid_semi_arid",
                         ec_e=6.0, esp=8.0),
            zone="arid_semi_arid")
        self.assertNotIn("ESP 8", _rec(recs, "Nitrogen")["solution"])

    def test_suppressed_recs_stay_deterministic(self):
        recs = recommend_soil(
            analyze_soil(100, 40, 600, 8.0, zone="arid_semi_arid", ec_e=6.0))
        for rec in recs:
            self.assertEqual(
                set(rec),
                {"element", "status", "deficit", "solution", "source", "lang"},
            )
            self.assertEqual(rec["source"], "rule-based")

    def test_no_zone_ec_e_is_harmless(self):
        items = analyze_soil(100, 40, 300, 6.5, ec_e=8.0, esp=20.0)
        self.assertFalse(any(i.get("suppressed") for i in items))
        recs = recommend_soil(items)
        self.assertNotIn("gypsum", _rec(recs, "Nitrogen")["solution"])

    def test_cost_lines_zero_under_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = config.DB_PATH
            config.DB_PATH = Path(tmp) / "sal_cost.db"
            db.init_db()
            try:
                cost = fertilizer_cost.estimate(
                    100, 40, 600, 8.0, zone="arid_semi_arid", ec_e=6.0)
                self.assertEqual(cost["total_per_ha"], 0.0)
                for line in cost["lines"]:
                    self.assertFalse(line["purchase"], line)
            finally:
                db.cleanup(config.DB_PATH)
                config.DB_PATH = prev


class TestSalinityApi(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "sal_api.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def test_predict_exposes_salinity_block(self):
        resp = client.post(
            "/api/predict",
            json={"N": 100, "P": 40, "K": 600, "ph": 8.0,
                  "agroecological_zone": "arid_semi_arid",
                  "ec_e": 6.5, "esp": 18.0},
        )
        self.assertEqual(resp.status_code, 200)
        sal = resp.json()["agroecology"]["salinity"]
        self.assertEqual(sal["ec_e_ds_m"], 6.5)
        self.assertEqual(sal["pause_threshold_ds_m"], 4.0)
        self.assertTrue(sal["fertilizer_paused"])
        self.assertTrue(sal["sodic"])
        self.assertEqual(sal["reclamation_product"], "gypsum")

    def test_predict_salinity_block_not_sodic_below_esp(self):
        resp = client.post(
            "/api/predict",
            json={"N": 100, "P": 40, "K": 600, "ph": 8.0,
                  "agroecological_zone": "arid_semi_arid",
                  "ec_e": 6.5, "esp": 8.0},
        )
        sal = resp.json()["agroecology"]["salinity"]
        self.assertTrue(sal["fertilizer_paused"])
        self.assertFalse(sal["sodic"])

    def test_predict_without_zone_ignores_ec_e(self):
        resp = client.post(
            "/api/predict", json={"N": 80, "P": 90, "K": 120, "ph": 6.8,
                                  "ec_e": 9.9})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("agroecology", resp.json())

    def test_negative_ec_e_rejected(self):
        resp = client.post("/api/predict",
                           json={"N": 1, "P": 1, "K": 1, "ph": 7, "ec_e": -1})
        self.assertEqual(resp.status_code, 422)

    def test_saved_saline_report_costs_nothing(self):
        pred = client.post(
            "/api/predict",
            json={"N": 100, "P": 40, "K": 600, "ph": 8.0,
                  "agroecological_zone": "arid_semi_arid",
                  "ec_e": 6.5, "esp": 18.0},
        ).json()
        rid = client.post(
            "/api/reports",
            json={"plot": "Saline", "N": 100, "P": 40, "K": 600, "ph": 8.0,
                  "fertility_status": pred["fertility_status"],
                  "recommendations": pred["recommendations"],
                  "agroecological_zone": "arid_semi_arid",
                  "ec_e": 6.5, "esp": 18.0},
        ).json()["id"]

        stored = db.get_report(rid)
        self.assertEqual(stored["ec_e"], 6.5)
        self.assertEqual(stored["esp"], 18.0)

        cost = client.get(f"/api/reports/{rid}/cost-estimate").json()
        self.assertEqual(cost["total_per_ha"], 0.0)
        for line in cost["lines"]:
            self.assertFalse(line["purchase"])

    def test_legacy_response_unchanged_without_ec_e(self):
        resp = client.post("/api/predict",
                           json={"N": 80, "P": 90, "K": 120, "ph": 6.8})
        self.assertNotIn("agroecology", resp.json())


if __name__ == "__main__":
    unittest.main()
