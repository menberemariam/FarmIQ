import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ethiopian_standards as es


class TestProfileSelection(unittest.TestCase):
    def test_default_profile_is_ethiosis(self):
        import os

        saved = os.environ.pop(es.PROFILE_ENV, None)
        try:
            self.assertEqual(es.get_profile(), "ethiosis")
        finally:
            if saved is not None:
                os.environ[es.PROFILE_ENV] = saved

    def test_invalid_profile_falls_back_to_default(self):
        import os

        saved = os.environ.get(es.PROFILE_ENV)
        os.environ[es.PROFILE_ENV] = "nonsense"
        try:
            self.assertEqual(es.get_profile(), "ethiosis")
        finally:
            if saved is None:
                os.environ.pop(es.PROFILE_ENV, None)
            else:
                os.environ[es.PROFILE_ENV] = saved


class TestUnitConverters(unittest.TestCase):
    def test_n_conversion_is_linear_and_invertible(self):
        self.assertEqual(es.n_pct_to_kg_ha(0.1), 400 * 0.1)
        for pct in (0.05, 0.1, 0.2, 0.35):
            self.assertAlmostEqual(es.n_kg_ha_to_pct(es.n_pct_to_kg_ha(pct)), pct)

    def test_p_conversion_uses_bulk_density_factor(self):
        # Decision: 1 mg/kg = 2.6 kg/ha (bulk density 1.3, 0-20 cm layer).
        self.assertEqual(es.p_mgkg_to_kg_ha(15), 39.0)
        self.assertEqual(es.p_mgkg_to_kg_ha(30), 78.0)
        self.assertEqual(es.p_kg_ha_to_mgkg(45.5), 17.5)

    def test_p_conversion_is_invertible(self):
        for mg in (5.0, 15.0, 30.0, 45.5, 100.0):
            self.assertAlmostEqual(es.p_kg_ha_to_mgkg(es.p_mgkg_to_kg_ha(mg)), mg)

    def test_p_conversion_factor_is_env_overridable(self):
        import os

        saved = os.environ.get(es.P_FACTOR_ENV)
        os.environ[es.P_FACTOR_ENV] = "1.0"
        try:
            self.assertEqual(es.p_mgkg_to_kg_ha(15), 15.0)  # legacy 1:1 rollback
        finally:
            if saved is None:
                os.environ.pop(es.P_FACTOR_ENV, None)
            else:
                os.environ[es.P_FACTOR_ENV] = saved

    def test_invalid_p_factor_falls_back_to_default(self):
        import os

        saved = os.environ.get(es.P_FACTOR_ENV)
        os.environ[es.P_FACTOR_ENV] = "not-a-number"
        try:
            self.assertEqual(es.get_p_factor(), 2.6)
        finally:
            if saved is None:
                os.environ.pop(es.P_FACTOR_ENV, None)
            else:
                os.environ[es.P_FACTOR_ENV] = saved

    def test_k_conversion_uses_cmolc_factor(self):
        self.assertAlmostEqual(es.k_cmolc_to_kg_ha(0.2), 78.2)
        self.assertAlmostEqual(es.k_cmolc_to_kg_ha(0.4), 156.4)
        self.assertAlmostEqual(es.k_kg_ha_to_cmolc(es.k_cmolc_to_kg_ha(0.3)), 0.3)


class TestBandPresets(unittest.TestCase):
    def test_generic_profile_matches_legacy_bands(self):
        labeling = es.labeling_bands("generic")
        advice = es.advice_ranges("generic")
        self.assertEqual(labeling["N"]["ideal"], (40.0, 60.0))
        self.assertEqual(labeling["K"]["ideal"], (60.0, 150.0))
        self.assertEqual(advice["K"], (100.0, 150.0))
        self.assertEqual(advice["ph"], (6.0, 7.0))

    def test_ethiosis_bands_follow_lab_thresholds(self):
        labeling = es.ethiosis_labeling_bands()
        advice = es.ethiosis_advice_ranges()
        # N: 0.15%-0.25% good core at factor 400
        self.assertEqual(labeling["N"]["ideal"], (60.0, 100.0))
        self.assertEqual(labeling["N"]["near"], (40.0, 120.0))
        # P: EthioSIS lab thresholds 15/30/45/60 mg/kg converted at 2.6 kg/ha
        # per mg/kg (bulk density 1.3 over 0-20 cm).
        self.assertEqual(labeling["P"]["ideal"], (78.0, 117.0))
        self.assertEqual(labeling["P"]["near"], (39.0, 156.0))
        # K: 0.2-0.4 cmolc/kg good core
        self.assertEqual(labeling["K"]["ideal"], (78.2, 156.4))
        # pH: EthioSIS good 6.5-7.5, moderate starts 5.5
        self.assertEqual(labeling["ph"]["ideal"], (6.5, 7.5))
        self.assertEqual(labeling["ph"]["near"][0], 5.5)
        self.assertEqual(advice["ph"], (6.5, 7.5))

    def test_ethiosis_p_bands_are_lab_thresholds_times_factor(self):
        factor = es.get_p_factor()
        labeling = es.ethiosis_labeling_bands()
        self.assertAlmostEqual(labeling["P"]["ideal"][0], 30.0 * factor)
        self.assertAlmostEqual(labeling["P"]["near"][0], 15.0 * factor)

    def test_near_bands_span_ideal_bands(self):
        for profile in ("ethiosis", "generic"):
            for col, bands in es.labeling_bands(profile).items():
                near_lo, near_hi = bands["near"]
                ideal_lo, ideal_hi = bands["ideal"]
                self.assertLessEqual(near_lo, ideal_lo, f"{profile}/{col}")
                self.assertLessEqual(ideal_hi, near_hi, f"{profile}/{col}")


class TestAgroecologicalZones(unittest.TestCase):
    EXPECTED = ("arid_semi_arid", "highland_nitisols", "highland_vertisols",
                "rift_valley")

    def test_zones_exist(self):
        self.assertEqual(es.list_zones(), list(self.EXPECTED))
        self.assertTrue(es.ZONES_JSON.exists())

    def test_unknown_zone_raises(self):
        with self.assertRaises(ValueError):
            es.zone_labeling_bands("cambisol")

    def test_zone_key_is_flexible(self):
        self.assertTrue(es.zone_exists("Highland Vertisols"))
        self.assertTrue(es.zone_exists("ARID/SEMI-ARID"))
        self.assertTrue(es.zone_exists("rift valley"))
        self.assertFalse(es.zone_exists(""))

    def test_zone_bands_shape_and_contiguity(self):
        for zone in es.list_zones():
            labeling = es.zone_labeling_bands(zone)
            advice = es.zone_advice_ranges(zone)
            self.assertEqual(set(labeling), {"N", "P", "K", "ph"}, zone)
            for col, bands in labeling.items():
                near_lo, near_hi = bands["near"]
                ideal_lo, ideal_hi = bands["ideal"]
                self.assertLessEqual(near_lo, ideal_lo, f"{zone}/{col}")
                self.assertLessEqual(ideal_hi, near_hi, f"{zone}/{col}")
                self.assertLessEqual(ideal_lo, ideal_hi, f"{zone}/{col}")
            self.assertEqual(
                advice, {col: b["ideal"] for col, b in labeling.items()}, zone
            )

    def test_zone1_nitisol_bands_match_source_table(self):
        bands = es.zone_labeling_bands("highland_nitisols")
        self.assertEqual(bands["N"]["ideal"], (130.0, 260.0))
        self.assertEqual(bands["P"]["ideal"], (39.0, 78.0))
        self.assertEqual(bands["K"]["ideal"], (260.0, 520.0))
        self.assertEqual(bands["ph"]["ideal"], (5.5, 6.5))

    def test_zone_p_bands_follow_bulk_density_factor(self):
        """Lab mg/kg values in the JSON must round-trip at the P factor."""
        factor = es.get_p_factor()
        for zone in es.list_zones():
            entry = es.zone_info(zone)
            lab = entry["bands"]["P"].get("lab_mg_kg")
            if not lab:
                continue
            ideal = entry["bands"]["P"]["ideal"]
            self.assertAlmostEqual(ideal[0], lab[0] * factor, places=1,
                                   msg=f"{zone} lower bound")
            self.assertAlmostEqual(ideal[1], lab[1] * factor, places=1,
                                   msg=f"{zone} upper bound")

    def test_zone_info_and_overrides(self):
        info = es.zone_info("highland_nitisols")
        self.assertIn("Al3", info["constraint"])
        self.assertEqual(info["source"], "EIAR / Jimma Agricultural Research Center (JARC); Haramaya University soil chemistry profiling")
        self.assertEqual(es.zone_overrides("highland_nitisols")["lime_first_ph"], 5.5)
        self.assertEqual(es.zone_overrides("highland_vertisols")["k_bypass_above"], 400.0)
        self.assertEqual(es.zone_overrides("rift_valley")["shift_blend_to"], "NPSZnB")
        self.assertEqual(es.zone_overrides("arid_semi_arid")["ec_e_pause_ds_m"], 4.0)
        self.assertEqual(es.zone_overrides("unknown-zone"), {})

    def test_zone_display_names(self):
        self.assertEqual(es.zone_display_name("rift_valley"), "Rift Valley Soils")
        self.assertEqual(es.zone_display_name("rift_valley", lang="am"),
                         "የሪፍት ሸለቆ አፈሮች")
        self.assertEqual(es.zone_display_name("nope"), "nope")


class TestCropMatrix(unittest.TestCase):
    def test_matrix_exists_and_covers_all_ml_crops(self):
        matrix = es.load_crop_matrix()
        self.assertTrue(matrix)
        missing = es.ml_crop_keys() - frozenset(matrix)
        self.assertEqual(missing, frozenset(), f"ML crops missing from matrix: {missing}")

    def test_staples_are_the_non_ml_crops(self):
        staples = es.ethiopian_staples()
        self.assertIn("teff", staples)
        self.assertIn("wheat", staples)
        self.assertIn("enset", staples)
        for crop in staples:
            self.assertNotIn(crop, es.ml_crop_keys())

    def test_every_entry_has_s1_s2_and_names(self):
        for crop, entry in es.load_crop_matrix().items():
            for key in ("S1", "S2", "N", "name_am", "name_en", "category"):
                self.assertIn(key, entry, f"{crop} missing {key}")
            self.assertIn("ph", entry["S1"], crop)
            self.assertIn("in_ml_model", entry, crop)


class TestCropSuitability(unittest.TestCase):
    def test_unknown_crop_returns_none(self):
        self.assertIsNone(es.crop_suitability("no-such-crop", ph=6.5))

    def test_no_parameters_returns_none(self):
        self.assertIsNone(es.crop_suitability("teff"))

    def test_teff_highland_conditions_are_s1(self):
        result = es.crop_suitability("teff", ph=6.5, temperature=18, rainfall=900)
        self.assertEqual(result["class"], "S1")

    def test_teff_too_hot_is_not_suitable(self):
        result = es.crop_suitability("teff", ph=6.5, temperature=32, rainfall=900)
        self.assertEqual(result["class"], "N")
        self.assertEqual(result["param_class"]["temperature"], "N")

    def test_teff_gap_conditions_are_marginal(self):
        # 22.5C sits between S1 (15-22) and S2 (23-27)
        result = es.crop_suitability("teff", ph=6.5, temperature=22.5, rainfall=900)
        self.assertEqual(result["class"], "S3")

    def test_worst_parameter_wins(self):
        # ph ideal but temperature violates N -> overall N
        result = es.crop_suitability("rice", ph=6.0, temperature=12, rainfall=1300)
        self.assertEqual(result["class"], "N")

    def test_ph_only_evaluation(self):
        result = es.crop_suitability("apple", ph=8.0)
        self.assertEqual(result["class"], "N")
        result = es.crop_suitability("apple", ph=6.4)
        self.assertEqual(result["class"], "S1")
        result = es.crop_suitability("apple", ph=5.7)
        self.assertEqual(result["class"], "S2")

    def test_disjoint_s2_ranges(self):
        # orange S2 pH has two windows: 5.5-5.9 or 7.1-7.8
        result = es.crop_suitability(
            "orange", ph=7.5, temperature=30, rainfall=800
        )
        self.assertEqual(result["class"], "S2")

    def test_boundary_values_are_inclusive(self):
        result = es.crop_suitability("maize", ph=5.8, temperature=18, rainfall=800)
        self.assertEqual(result["class"], "S1")
        result = es.crop_suitability("maize", ph=5.7, temperature=18, rainfall=800)
        self.assertEqual(result["class"], "S2")
        # between S2 top (5.7) and S1 bottom (5.8) -> marginal
        result = es.crop_suitability("maize", ph=5.75, temperature=18, rainfall=800)
        self.assertEqual(result["class"], "S3")

    def test_result_shape(self):
        result = es.crop_suitability("maize", ph=6.2, temperature=22, rainfall=900)
        self.assertEqual(
            set(result), {"crop", "class", "param_class", "checks", "reasons"}
        )
        self.assertEqual(result["crop"], "maize")
        self.assertEqual(len(result["checks"]), 3)
        for check in result["checks"]:
            self.assertEqual(
                set(check), {"param", "value", "class", "band"}
            )
        self.assertTrue(result["reasons"])

    def test_n_exclusion_boundaries_are_strict(self):
        # rice N: pH < 4.5 excludes, pH 4.5 exactly does not
        self.assertEqual(
            es.crop_suitability("rice", ph=4.4)["param_class"]["ph"], "N"
        )
        result = es.crop_suitability("rice", ph=4.5, temperature=25, rainfall=1300)
        # 4.5 is outside S1 (5.5-6.5) and S2 (6.6-7.3) but not excluded -> S3
        self.assertEqual(result["param_class"]["ph"], "S3")
        self.assertEqual(result["class"], "S3")


class TestMatrixJsonShape(unittest.TestCase):
    def test_json_is_valid_and_sorted_keys(self):
        raw = json.loads(
            (PROJECT_ROOT / "Dataset" / "ethiopian_crop_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(raw), 34)
        for crop, entry in raw.items():
            self.assertIsInstance(entry["S1"], dict, crop)
            self.assertIsInstance(entry["N"], dict, crop)


if __name__ == "__main__":
    unittest.main()
