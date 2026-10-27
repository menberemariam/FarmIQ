"""Afaan Oromoo (om) localization tests — additive only.

Covers: i18n STRINGS/LANGUAGES parity and om aliases, the engine's
_OM_TEMPLATES key parity and rendering, application schedules and
micronutrient/selection messages, crop/product name_om data files,
zone/soil-type display names, and the API lang whitelist + additive
response fields. Existing en/am behavior is pinned by the original suite;
these tests prove om lands without disturbing it.
"""

import json
import re
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

# Point the API at a throwaway DB before importing it.
_tmpdir = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmpdir.name) / "test_om_i18n.db"

import api  # noqa: E402
import ethiopian_standards as es  # noqa: E402
import fertilizer_catalog  # noqa: E402
import i18n  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import (  # noqa: E402
    STATUS_CANONICAL,
    _AM_TEMPLATES,
    _EN_TEMPLATES,
    _MICRO_MESSAGES,
    _OM_TEMPLATES,
    _SEL_MESSAGES,
    _APPLICATION_SCHEDULES,
    analyze_soil,
    application_schedule,
    build_prompt,
    merge_recommendations,
    micronutrient_check,
    recommend_soil,
)

client = TestClient(api.app)

# Fixed deficient-acid analysis used across rendering tests.
SAMPLE = {"N": 20.0, "P": 58.0, "K": 60.0, "ph": 4.8}
CROP = "maize"


class TestI18nOromo(unittest.TestCase):
    def test_language_registered(self):
        self.assertEqual(i18n.LANGUAGES["om"], "Afaan Oromoo (Oromo)")

    def test_strings_full_parity_with_en(self):
        en = set(i18n.STRINGS["en"])
        om = set(i18n.STRINGS["om"])
        self.assertEqual(en - om, set(), f"om missing: {en - om}")
        self.assertEqual(om - en, set(), f"om extra: {om - en}")

    def test_strings_om_nonempty_and_format_safe(self):
        for key, text in i18n.STRINGS["om"].items():
            self.assertTrue(text.strip(), key)
            en_ph = set(re.findall(r"\{(\w+)", i18n.STRINGS["en"][key]))
            om_ph = set(re.findall(r"\{(\w+)", text))
            self.assertEqual(en_ph, om_ph, f"placeholder drift in {key}")

    def test_translate_om_and_fallback(self):
        self.assertIn("Nooraa lafaa", i18n.translate("om", "zone_lime_first", ph="5.0"))
        self.assertEqual(i18n.translate("om", "no_such_key"), "no_such_key")
        # Legacy languages untouched.
        self.assertEqual(i18n.translate("en", "good"), "Good")
        self.assertIn("የአፈር", i18n.translate("am", "title"))

    def test_normalize_lang_om_aliases(self):
        for raw in ("om", "OM", "orm", "om-et", "Oromo", "Afaan Oromoo",
                    "AFAAN OROMOO", "afaan oromia", "Oromiffa", "Qubee"):
            self.assertEqual(i18n.normalize_lang(raw), "om", raw)

    def test_normalize_lang_legacy_unchanged(self):
        for raw, want in (("am", "am"), ("አማርኛ", "am"), ("en", "en"),
                          ("xx", "en"), ("", "en"), (None, "en")):
            self.assertEqual(i18n.normalize_lang(raw), want)

    def test_normalize_qubee_nfc_and_cleanup(self):
        decomposed = unicodedata.normalize("NFD", "Xaafii")
        self.assertEqual(i18n.normalize_qubee(decomposed), "Xaafii")
        self.assertEqual(i18n.normalize_qubee("  Muuzii​ "), "Muuzii")
        self.assertIsNone(i18n.normalize_qubee(None))

    def test_terms_and_crop_names_present(self):
        self.assertTrue(i18n.TERMS_OM["soil"].startswith("Biyyee"))
        self.assertEqual(i18n.CROP_NAMES_OM["maize"], "Boqqolloon (maize)")
        self.assertEqual(i18n.CROP_NAMES_OM["teff"], "Xaafii (teff)")

    def test_crop_names_cover_matrix_and_ml_crops(self):
        matrix = es.load_crop_matrix()
        self.assertEqual(set(matrix), set(i18n.CROP_NAMES_OM),
                         "CROP_NAMES_OM must cover all 34 matrix crops")
        self.assertEqual(es.ml_crop_keys() - set(i18n.CROP_NAMES_OM), set())


class TestDataFiles(unittest.TestCase):
    def test_terminology_om_json_loads(self):
        data = json.loads(
            (PROJECT_ROOT / "Dataset" / "terminology_om.json").read_text(
                encoding="utf-8"))
        self.assertEqual(data["language"]["code"], "om")
        self.assertGreaterEqual(len(data["soil_terms"]), 15)
        self.assertGreaterEqual(len(data["actions"]), 10)
        self.assertEqual(data["status_words"]["deficient"], ["hanqina", "hanqataa"])

    def test_every_matrix_crop_has_name_om(self):
        for crop, entry in es.load_crop_matrix().items():
            name = entry.get("name_om")
            self.assertTrue(name, f"{crop} missing name_om")
            self.assertNotIn("((", name, f"{crop} malformed name_om")
            self.assertTrue(name.isascii(), f"{crop} name_om must be Qubee/Latin")

    def test_every_catalog_product_has_name_om(self):
        for product in fertilizer_catalog.get_products():
            name = product.get("name_om")
            self.assertTrue(name, f"{product['id']} missing name_om")
            self.assertTrue(name.isascii())

    def test_zone_and_soil_type_name_om(self):
        for zone in es.list_zones():
            self.assertTrue(es.zone_display_name(zone, lang="om"), zone)
        for soil in es.list_soil_types():
            self.assertTrue(es.soil_type_display_name(soil, lang="om"), soil)


class TestEngineOromo(unittest.TestCase):
    def _analysis(self, **overrides):
        params = {**SAMPLE, "crop": CROP}
        params.update(overrides)
        return analyze_soil(**params)

    def test_template_key_parity(self):
        self.assertEqual(set(_EN_TEMPLATES), set(_AM_TEMPLATES))
        self.assertEqual(set(_EN_TEMPLATES), set(_OM_TEMPLATES))
        self.assertEqual(len(_OM_TEMPLATES), 15)

    def test_render_four_recs_no_placeholder_leaks(self):
        analysis = self._analysis()
        recs = recommend_soil(analysis, lang="om")
        self.assertEqual(len(recs), 4)
        for rec in recs:
            self.assertEqual(rec["lang"], "om")
            self.assertNotIn("{", rec["solution"], rec["element"])
            self.assertTrue(rec["solution"].strip())
            self.assertEqual(rec["source"], "rule-based")

    def test_statuses_match_en_and_om_is_latin(self):
        analysis = self._analysis()
        en = recommend_soil(analysis, lang="en")
        om = recommend_soil(analysis, lang="om")
        am = recommend_soil(analysis, lang="am")
        self.assertEqual([r["status"] for r in en], [r["status"] for r in om])
        self.assertEqual([r["status"] for r in en], [r["status"] for r in am])
        om_text = " ".join(r["solution"] for r in om)
        # Qubee: no Ethiopic script may leak into om advice.
        self.assertFalse(
            any("ETHIOPIC" in unicodedata.name(ch, "") for ch in om_text),
            "Ethiopic characters leaked into om output")
        self.assertIn("Naayitroojinii", om_text)
        # Legacy surfaces still produce their own script.
        self.assertIn("Nitrogen is", en[0]["solution"])
        self.assertIn("ናይትሮጅን", am[0]["solution"])
        self.assertNotIn("ናይትሮጅን", om_text)

    def test_crop_name_inserted_in_om(self):
        recs = recommend_soil(self._analysis(), lang="om")
        self.assertIn("Boqqolloon", recs[0]["solution"])

    def test_lemmas_use_glossary_terms(self):
        # Deficient N and P, ample K, acidic pH.
        analysis = self._analysis(N=20.0, P=20.0, K=90.0, ph=4.6)
        recs = {r["element"]: r["solution"] for r in
                recommend_soil(analysis, lang="om")}
        self.assertIn("Nooraa lafaa", recs["pH"])          # lime
        self.assertIn("t/ha", recs["pH"])
        self.assertIn("Yuuriyaa", recs["Nitrogen"])        # urea
        self.assertIn("irra-naqinsa", recs["Nitrogen"])    # top-dress term
        self.assertIn("booyoo facaasaatti", recs["Phosphorus"])  # seed furrow

    def test_zone_and_lime_notes_om(self):
        analysis = self._analysis(ph=4.6, zone="highland_nitisols",
                                  soil_type="nitisol")
        recs = recommend_soil(analysis, lang="om")
        ph_text = next(r["solution"] for r in recs if r["element"] == "pH")
        self.assertIn("Nooraa lafaa", ph_text)      # lime-first zone note
        self.assertIn("LRF", ph_text)               # LRF schedule note

    def test_salinity_suppression_om(self):
        analysis = self._analysis(
            zone="arid_semi_arid",
            ec_e=6.0,
            esp=20.0,
        )
        recs = recommend_soil(analysis, lang="om")
        n_item = next(r for r in analysis if r["col"] == "N")
        self.assertTrue(n_item.get("suppressed"))
        n_text = next(r["solution"] for r in recs if r["element"] == "Nitrogen")
        self.assertIn("jiipsemii", n_text.lower())

    def test_status_canonical_om_words(self):
        self.assertEqual(STATUS_CANONICAL["hanqina"], "deficient")
        self.assertEqual(STATUS_CANONICAL["hanqataa"], "deficient")
        self.assertEqual(STATUS_CANONICAL["gahaa"], "adequate")
        self.assertEqual(STATUS_CANONICAL["gar-malee hedduu"], "ample")
        self.assertEqual(STATUS_CANONICAL["ol-aanaa"], "ample")

    def test_micro_and_selection_messages_om(self):
        micro = micronutrient_check(sulfur_ppm=10.0, boron_ppm=0.2, lang="om")
        self.assertIn("salfarii", micro["sulfur"]["message"])
        self.assertIn("booronii", micro["boron"]["message"])
        for key in _SEL_MESSAGES["en"]:
            self.assertIn(key, _SEL_MESSAGES["om"])
        for key in _MICRO_MESSAGES["en"]:
            self.assertIn(key, _MICRO_MESSAGES["om"])

    def test_application_schedules_om(self):
        self.assertEqual(set(_APPLICATION_SCHEDULES),
                         {"urea", "nps", "npsb", "dap", "potassium_sulfate",
                          "blended", "lime", "sulfur"})
        for product in _APPLICATION_SCHEDULES:
            schedule = application_schedule(product, lang="om")
            self.assertEqual(schedule["lang"], "om")
            self.assertTrue(schedule["splits"])
            for restriction in schedule["restrictions"]:
                self.assertTrue(restriction.strip())
        maintenance = application_schedule(None, lang="om")
        self.assertIn("eegalaan", maintenance["note"])

    def test_build_prompt_carries_om_instruction(self):
        prompt = build_prompt(self._analysis(), lang="om")
        self.assertIn("Afaan Oromoo", prompt)
        # en/am prompts unchanged by the om branch.
        self.assertNotIn("Afaan Oromoo", build_prompt(self._analysis(), lang="en"))
        self.assertIn("አማርኛ", build_prompt(self._analysis(), lang="am"))

    def test_merge_falls_back_to_om_deterministic(self):
        analysis = self._analysis()
        deterministic = recommend_soil(analysis, lang="om")
        merged = merge_recommendations({}, deterministic, analysis=analysis,
                                       lang="om")
        self.assertEqual([r["solution"] for r in merged],
                         [r["solution"] for r in deterministic])
        self.assertTrue(all(r["lang"] == "om" for r in merged))

    def test_merge_accepts_valid_om_llm_and_normalizes(self):
        analysis = self._analysis()
        deterministic = recommend_soil(analysis, lang="om")
        lo, hi = analysis[0]["band"]
        llm = {
            "Nitrogen": {
                "status": "deficient",
                # Restates the deficit within tolerance; has a stray
                # zero-width character that normalize_qubee must strip.
                "solution": f"Naayitroojinii {analysis[0]['deficit']:.1f} kg/ha "
                            f"hanqataa​ — Yuuriyaa {lo:.0f}-{hi:.0f} eegali.",
            },
        }
        merged = merge_recommendations(llm, deterministic, analysis=analysis,
                                       lang="om")
        self.assertEqual(merged[0]["source"], "llm")
        self.assertNotIn("​", merged[0]["solution"])

    def test_en_am_byte_stable_sample(self):
        # Regression pin: legacy languages still render their own templates.
        analysis = self._analysis()
        en = recommend_soil(analysis, lang="en")
        am = recommend_soil(analysis, lang="am")
        self.assertTrue(en[0]["solution"].startswith("Nitrogen is"))
        self.assertTrue(all(r["lang"] == "en" for r in en))
        self.assertTrue(all(r["lang"] == "am" for r in am))


class TestEthioStandardsOromo(unittest.TestCase):
    def test_zone_display_name_om(self):
        self.assertEqual(es.zone_display_name("rift_valley", lang="om"),
                         "Afa Rift Valley")
        self.assertEqual(es.zone_display_name("rift_valley"), "Rift Valley Soils")
        self.assertEqual(es.zone_display_name("rift_valley", lang="am"),
                         "የሪፍት ሸለቆ አፈሮች")

    def test_soil_type_display_name_om(self):
        self.assertEqual(es.soil_type_display_name("nitisol", lang="om"),
                         "Nitisolii")
        self.assertEqual(es.soil_type_display_name("nitisol"), "Nitisols")

    def test_canonical_soil_type_accepts_om_name(self):
        self.assertEqual(es.canonical_soil_type("Nitisolii"), "nitisol")
        self.assertEqual(es.canonical_soil_type("Vertisolii"), "vertisol")


class TestApiOromo(unittest.TestCase):
    def _predict(self, lang, **extra):
        return client.post(
            "/api/predict",
            json={"N": 25, "P": 10, "K": 40, "ph": 5.5, "crop": "rice",
                  "lang": lang, **extra},
        )

    def test_predict_lang_om(self):
        resp = self._predict("om")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["lang"], "om")
        self.assertTrue(body["recommendations"])
        for rec in body["recommendations"]:
            self.assertEqual(rec["lang"], "om")
            self.assertNotIn("ናይትሮጅን", rec["solution"])
        self.assertIn("Naayitroojinii", body["recommendations"][0]["solution"])

    def test_predict_lang_om_aliases(self):
        for alias in ("Afaan Oromoo", "Oromiffa", "orm", "Qubee"):
            resp = self._predict(alias)
            self.assertEqual(resp.status_code, 200, alias)
            self.assertEqual(resp.json()["lang"], "om", alias)

    def test_predict_legacy_langs_unchanged(self):
        for lang, marker in (("en", "Nitrogen is"), ("am", "ናይትሮጅን")):
            resp = self._predict(lang)
            self.assertEqual(resp.status_code, 200, lang)
            body = resp.json()
            self.assertEqual(body["lang"], lang)
            self.assertIn(marker, body["recommendations"][0]["solution"])

    def test_predict_unknown_lang_still_422(self):
        resp = self._predict("klingon")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("en, am, om", resp.json()["detail"])

    def test_crops_expose_name_om(self):
        crops = client.get("/api/crops").json()["crops"]
        self.assertEqual(len(crops), 22)
        rice = next(c for c in crops if c["name"] == "rice")
        self.assertEqual(rice["name_om"], "Ruusii (rice)")
        for crop in crops:
            self.assertTrue(crop["name_om"], crop["name"])

    def test_ethiopian_crops_expose_name_om(self):
        crops = client.get("/api/crops/ethiopian").json()["crops"]
        self.assertEqual(len(crops), 34)
        teff = next(c for c in crops if c["name"] == "teff")
        self.assertEqual(teff["name_om"], "Xaafii (teff)")
        # Legacy fields still present.
        self.assertEqual(teff["name_am"], "ትፍ (teff)")
        self.assertEqual(teff["name_en"], "Teff")

    def test_application_guide_om(self):
        resp = self._predict("om", include_application_guide=True)
        self.assertEqual(resp.status_code, 200)
        for rec in resp.json()["recommendations"]:
            guide = rec.get("application")
            if guide:
                self.assertEqual(guide["lang"], "om")


if __name__ == "__main__":
    unittest.main()
