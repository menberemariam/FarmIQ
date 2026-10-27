import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from ML.engine import (
    analyze_soil,
    climate_medians,
    coerce_element_record,
    crop_feature_row,
    build_prompt,
    llm_fact_failures,
    merge_recommendations,
    normalize_agronomic_keys,
    normalize_status,
    parse_llm_json,
    parse_llm_json_partial,
    recommend_soil,
    training_range_warnings,
)


class TestAnalyzeSoil(unittest.TestCase):
    def test_statuses(self):
        analysis = analyze_soil(25, 10, 40, 5.5)
        statuses = {a["element"]: a["status"] for a in analysis}
        self.assertEqual(
            statuses,
            {
                "Nitrogen": "deficient",
                "Phosphorus": "deficient",
                "Potassium": "deficient",
                "pH": "deficient",
            },
        )

    def test_adequate_and_ample(self):
        analysis = analyze_soil(80, 90, 120, 6.5)
        statuses = {a["element"]: a["status"] for a in analysis}
        self.assertEqual(
            statuses,
            {
                "Nitrogen": "adequate",
                "Phosphorus": "adequate",
                "Potassium": "adequate",
                "pH": "adequate",
            },
        )
        analysis = analyze_soil(110, 130, 180, 8.5)
        statuses = {a["element"]: a["status"] for a in analysis}
        self.assertEqual(
            statuses,
            {
                "Nitrogen": "ample",
                "Phosphorus": "ample",
                "Potassium": "ample",
                "pH": "ample",
            },
        )

    def test_deficit_is_positive_and_correct(self):
        analysis = analyze_soil(30, 10, 40, 5.5)
        by = {a["col"]: a for a in analysis}
        # EthioSIS bands in model units: N 60-100, P 78-117 (30-45 mg/kg x 2.6),
        # K 78.2-156.4, pH 6.5-7.5
        self.assertAlmostEqual(by["N"]["deficit"], 30.0)
        self.assertAlmostEqual(by["P"]["deficit"], 68.0)
        self.assertAlmostEqual(by["K"]["deficit"], 38.2)
        self.assertAlmostEqual(by["ph"]["deficit"], 1.0)


class TestRecommendSoil(unittest.TestCase):
    def test_output_shape_and_amounts(self):
        analysis = analyze_soil(25, 10, 40, 5.5)
        recs = recommend_soil(analysis)
        self.assertEqual(len(recs), 4)
        for rec in recs:
            self.assertIn(rec["element"], ["Nitrogen", "Phosphorus", "Potassium", "pH"])
            self.assertIn(rec["status"], ["deficient", "ample", "adequate"])
            self.assertGreater(len(rec["solution"]), 50, f"{rec['element']}: solution too short")
            self.assertEqual(rec["source"], "rule-based")

    def test_amounts_follow_deficit(self):
        """Urea kg/ha for a bigger N deficit must be bigger (46% N basis)."""
        import re

        def first_kg_ha(text):
            match = re.search(r"([\d.]+)\s*kg/ha", text)
            return float(match.group(1))

        small = recommend_soil(analyze_soil(35, 20, 120, 6.5))[0]
        big = recommend_soil(analyze_soil(10, 20, 120, 6.5))[0]
        self.assertGreater(first_kg_ha(big["solution"]), first_kg_ha(small["solution"]))

    def test_adequate_means_no_heavy_dosing(self):
        recs = recommend_soil(analyze_soil(80, 90, 120, 6.5))
        for rec in recs:
            self.assertNotIn("deficient", rec["status"])


class TestPrompt(unittest.TestCase):
    def test_prompt_contains_data_and_ranges(self):
        prompt = build_prompt(analyze_soil(25, 10, 40, 5.5))
        self.assertIn("Nitrogen", prompt)
        self.assertIn("deficient", prompt)
        # EthioSIS generic bands rendered by _band_text
        self.assertIn("60-100", prompt)
        self.assertIn("78-117", prompt)   # P lab 30-45 mg/kg at 2.6 kg/ha
        self.assertIn("78-156", prompt)
        self.assertIn("6.5-7.5", prompt)

    def test_prompt_requires_100_word_solutions(self):
        prompt = build_prompt(analyze_soil(25, 10, 40, 5.5))
        self.assertIn("minimum 100 words", prompt)

    def test_prompt_carries_ethiopian_context_and_guardrail(self):
        prompt = build_prompt(analyze_soil(
            25, 10, 40, 5.5, zone="highland_nitisols", soil_type="nitisol"))
        self.assertIn("Ethiopian context", prompt)
        self.assertIn("highland_nitisols", prompt)
        self.assertIn("nitisol", prompt)
        self.assertIn("do not substitute products", prompt)

    def test_prompt_without_context_has_no_guardrail(self):
        prompt = build_prompt(analyze_soil(25, 10, 40, 5.5))
        self.assertNotIn("Ethiopian context", prompt)
        self.assertNotIn("do not substitute products", prompt)


class TestParseLlJson(unittest.TestCase):
    def test_plain_json(self):
        out = parse_llm_json('{"Nitrogen": {"status": "deficient", "solution": "x"}}')
        self.assertEqual(out["Nitrogen"]["status"], "deficient")

    def test_markdown_fences(self):
        out = parse_llm_json(
            '```json\n{"Nitrogen": {"status": "ample", "solution": "y"}}\n```'
        )
        self.assertEqual(out["Nitrogen"]["status"], "ample")

    def test_surrounding_text(self):
        out = parse_llm_json('Here you go: {"pH": {"status": "adequate", "solution": "z"}} thanks!')
        self.assertEqual(out["pH"]["status"], "adequate")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_llm_json("no json here")


class TestContractNormalizer(unittest.TestCase):
    def test_case_and_whitespace_keys_are_canonical(self):
        parsed = {
            "nitrogen": {"status": "deficient", "solution": "n"},
            " PH ": {"status": "deficient", "solution": "p"},
            "Phosphorus": {"status": "deficient", "solution": "ph"},
            "potassium": {"status": "deficient", "solution": "k"},
        }
        out = normalize_agronomic_keys(parsed)
        self.assertEqual(set(out), {"Nitrogen", "Phosphorus", "Potassium", "pH"})
        self.assertEqual(out["pH"]["solution"], "p")

    def test_unknown_keys_and_bad_records_dropped(self):
        parsed = {
            "nitrate": {"status": "deficient", "solution": "fuzzy is not allowed"},
            "Nitrogen": "not a dict",
            "pH": {"status": "deficient", "solution": "   "},
            "Potassium": {"status": "", "solution": "missing status"},
        }
        self.assertEqual(normalize_agronomic_keys(parsed), {})

    def test_normalization_is_idempotent(self):
        parsed = {"nitrogen": {"status": " deficient ", "solution": " n "}}
        once = normalize_agronomic_keys(parsed)
        self.assertEqual(normalize_agronomic_keys(once), once)
        self.assertEqual(once["Nitrogen"], {"status": "deficient", "solution": "n"})

    def test_coerce_rejects_non_dict_and_empty_solution(self):
        self.assertIsNone(coerce_element_record("text"))
        self.assertIsNone(coerce_element_record({"status": "deficient", "solution": ""}))
        self.assertEqual(
            coerce_element_record({"status": "deficient", "solution": " x "}),
            {"status": "deficient", "solution": "x"},
        )


class TestPartialParse(unittest.TestCase):
    def test_truncated_response_recovers_complete_elements(self):
        content = (
            '```json\n{"Nitrogen": {"status": "deficient", "solution": "n"}, '
            '"Phosphorus": {"status": "deficient", "solution": "p"}, '
            '"Potassium": {"status": "deficient", "solution": "k"}, '
            '"pH": {"status": "def'
        )
        out = parse_llm_json_partial(content)
        self.assertEqual(set(out), {"Nitrogen", "Phosphorus", "Potassium"})

    def test_garbage_returns_empty(self):
        self.assertEqual(parse_llm_json_partial("no json here"), {})

    def test_recovered_elements_merge_and_missing_falls_back(self):
        analysis = analyze_soil(25, 10, 40, 5.5)
        recs = recommend_soil(analysis)
        content = (
            '{"Nitrogen": {"status": "deficient", "solution": "n"}, '
            '"Phosphorus": {"status": "deficient", "solution": "p"}, '
            '"Potassium": {"status": "deficient", "solution": "k"}, '
            '"pH": {"status": "def'
        )
        merged = merge_recommendations(parse_llm_json_partial(content), recs)
        by = {r["element"]: r for r in merged}
        self.assertEqual(by["Nitrogen"]["source"], "llm")
        self.assertEqual(by["Phosphorus"]["source"], "llm")
        self.assertEqual(by["Potassium"]["source"], "llm")
        self.assertEqual(by["pH"]["source"], "rule-based")


class TestMergeRecommendations(unittest.TestCase):
    def test_consistent_llm_wins(self):
        analysis = analyze_soil(25, 10, 40, 5.5)
        recs = recommend_soil(analysis)
        llm = {
            "Nitrogen": {"status": "deficient", "solution": "LLM text"},
            "Phosphorus": {"status": "deficient", "solution": "LLM text"},
            "Potassium": {"status": "deficient", "solution": "LLM text"},
            "pH": {"status": "deficient", "solution": "LLM text"},
        }
        merged = merge_recommendations(llm, recs)
        self.assertTrue(all(r["source"] == "llm" for r in merged))

    def test_inconsistent_llm_falls_back(self):
        analysis = analyze_soil(25, 10, 40, 5.5)
        recs = recommend_soil(analysis)
        llm = {"Nitrogen": {"status": "ample", "solution": "contradicts computed status"}}
        merged = merge_recommendations(llm, recs)
        by = {r["element"]: r for r in merged}
        self.assertEqual(by["Nitrogen"]["source"], "rule-based")
        self.assertEqual(by["Nitrogen"]["status"], "deficient")

    def test_missing_element_falls_back(self):
        analysis = analyze_soil(25, 10, 40, 5.5)
        recs = recommend_soil(analysis)
        merged = merge_recommendations({}, recs)
        self.assertTrue(all(r["source"] == "rule-based" for r in merged))

    def test_lowercase_and_spaced_keys_merge_as_llm(self):
        analysis = analyze_soil(25, 10, 40, 5.5)
        recs = recommend_soil(analysis)
        llm = {
            "nitrogen": {"status": "deficient", "solution": "n"},
            " phosphorus ": {"status": "deficient", "solution": "p"},
            "POTASSIUM": {"status": "deficient", "solution": "k"},
            "PH": {"status": "deficient", "solution": "ph"},
        }
        merged = merge_recommendations(llm, recs)
        self.assertTrue(all(r["source"] == "llm" for r in merged))


class TestAmharicRecommendations(unittest.TestCase):
    """All 12 Amharic templates must render completely, in Ge'ez, with the
    computed amounts, no leftover placeholders, and formal register."""

    def test_all_templates_render(self):
        samples = [
            (25, 10, 40, 5.5),          # everything deficient
            (110, 80, 180, 8.5),        # everything ample
            (80, 40, 120, 6.5),         # everything adequate
        ]
        seen = set()
        for N, P, K, ph in samples:
            for crop in (None, "rice"):
                recs = recommend_soil(analyze_soil(N, P, K, ph, crop=crop), lang="am")
                self.assertEqual(len(recs), 4)
                for rec in recs:
                    self.assertIn("lang", rec)
                    self.assertEqual(rec["lang"], "am")
                    seen.add(f"{rec['element']}:{rec['status']}")
                    self.assertGreater(len(rec["solution"]), 50)
                    self.assertNotIn("{", rec["solution"], "unfilled placeholder")
                    self.assertRegex(rec["solution"], r"[\u1200-\u137F]", "no Ge'ez")
        self.assertEqual(len(seen), 12, f"expected 12 template variants, got {seen}")

    def test_numbers_match_engine_amounts(self):
        analysis = analyze_soil(25, 10, 40, 5.5, crop="rice")
        am = {r["element"]: r for r in recommend_soil(analysis, lang="am")}
        n_text = am["Nitrogen"]["solution"]
        self.assertIn("110.9", n_text)  # urea 51.0/0.46
        self.assertIn("51.0", n_text)   # deficit
        self.assertIn("76-84", n_text)  # rice N band
        self.assertIn("2.8", am["pH"]["solution"])  # lime t/ha for 0.69 pH gap (0.69/0.5*2)

    def test_bilingual_terms_and_register(self):
        recs = recommend_soil(analyze_soil(25, 10, 40, 5.5, crop="rice"), lang="am")
        all_text = " ".join(r["solution"] for r in recs)
        for term in ("ዩሪያ (Urea", "ዲኤፒ (DAP", "ኤንፒኤስ (NPS", "ናይትሮጅን (Nitrogen", "ለሩዝ (rice)"):
            self.assertIn(term, all_text)
        self.assertIn("።", all_text)  # Ethiopic full stop
        self.assertRegex(all_text, r"ይተግብሩ")  # polite imperative register

    def test_prompt_amharic_instruction(self):
        prompt = build_prompt(analyze_soil(25, 10, 40, 5.5), lang="am")
        self.assertIn("አማርኛ", prompt)
        self.assertIn("።", prompt)
        self.assertNotIn("አማርኛ", build_prompt(analyze_soil(25, 10, 40, 5.5), lang="en"))


class TestLlmAmharicValidation(unittest.TestCase):
    def setUp(self):
        self.analysis = analyze_soil(25, 10, 40, 5.5, crop="rice")
        self.det = recommend_soil(self.analysis, lang="am")

    def test_correct_amharic_accepted_and_normalized(self):
        llm = {
            "Nitrogen": {
                "status": "deficient",
                "solution": "ናይትሮጅን (Nitrogen) በ51.0 ኪግ/ሄክታር ያነሰ ነው። ዩሪያ (Urea) በ110.9 ኪግ/ሄክታር ይተግብሩ።",
            }
        }
        merged = merge_recommendations(llm, self.det, analysis=self.analysis, lang="am")
        by = {r["element"]: r for r in merged}
        self.assertEqual(by["Nitrogen"]["source"], "llm")
        self.assertIn("ናይትሮጅን", by["Nitrogen"]["solution"])

    def test_wrong_numbers_fall_back(self):
        """The LLM states a band/amount that contradicts the computed facts ->
        that element falls back to the deterministic Amharic template."""
        llm = {
            "Nitrogen": {
                "status": "deficient",
                "solution": "ናይትሮጅን (Nitrogen) በ999.0 ኪግ/ሄክታር ያነሰ ነው። ዩሪያ (Urea) በ999.0 ኪግ/ሄክታር ይተግብሩ።",
            }
        }
        failures = llm_fact_failures(self.analysis, llm)
        self.assertIn("Nitrogen", failures)
        merged = merge_recommendations(llm, self.det, analysis=self.analysis, lang="am")
        by = {r["element"]: r for r in merged}
        self.assertEqual(by["Nitrogen"]["source"], "rule-based")
        self.assertIn("ዩሪያ (Urea, 46% N)", by["Nitrogen"]["solution"])

    def test_orthographic_variant_status_normalized(self):
        llm = {
            "Phosphorus": {
                "status": "deficient",
                "solution": "ፎስፎረስ (Phosphorus) በ21.7 ኪግ/ሄክታር ያነሰ ነው። ዲኤፒ (DAP) ይተግብሩ።",
            }
        }
        merged = merge_recommendations(llm, self.det, analysis=self.analysis, lang="am")
        by = {r["element"]: r for r in merged}
        self.assertEqual(by["Phosphorus"]["source"], "rule-based")  # 21.7 contradicts facts
        self.assertIn("ዲኤፒ (DAP", by["Phosphorus"]["solution"])


class TestNormalizeStatus(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_status("Low"), "deficient")
        self.assertEqual(normalize_status("HIGH"), "ample")
        self.assertEqual(normalize_status("optimal"), "adequate")
        self.assertEqual(normalize_status("not a status"), None)


class TestCropFeatureRow(unittest.TestCase):
    def test_seven_columns_in_model_order(self):
        row = crop_feature_row(25, 10, 40, 5.5)
        self.assertEqual(list(row.columns), config.CROP_FEATURES)
        self.assertEqual(len(row), 1)

    def test_missing_climate_filled_with_dataset_medians(self):
        med = climate_medians()
        row = crop_feature_row(25, 10, 40, 5.5)
        self.assertEqual(row["temperature"][0], med["temperature"])
        self.assertEqual(row["humidity"][0], med["humidity"])
        self.assertEqual(row["rainfall"][0], med["rainfall"])

    def test_explicit_climate_kept(self):
        row = crop_feature_row(25, 10, 40, 5.5, temperature=22.0, humidity=40.0, rainfall=90.0)
        self.assertEqual(row["temperature"][0], 22.0)
        self.assertEqual(row["humidity"][0], 40.0)
        self.assertEqual(row["rainfall"][0], 90.0)


class TestTrainingRangeWarnings(unittest.TestCase):
    def test_in_range_values_have_no_warnings(self):
        self.assertEqual(training_range_warnings(25, 10, 40, 5.5), [])

    def test_out_of_range_fields_flagged(self):
        warnings = training_range_warnings(155, 10, 40, 5.5)
        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertEqual(w["field"], "N")
        self.assertEqual(w["value"], 155)
        self.assertEqual(w["range"], [0, 140])
        self.assertIn("N=155", w["message"])

    def test_amharic_message_localized(self):
        warnings = training_range_warnings(155, 10, 40, 5.5, lang="am")
        self.assertRegex(warnings[0]["message"], r"[\u1200-\u137F]")


if __name__ == "__main__":
    unittest.main()
