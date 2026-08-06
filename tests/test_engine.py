import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ML.engine import (
    analyze_soil,
    build_prompt,
    merge_recommendations,
    normalize_status,
    parse_llm_json,
    recommend_soil,
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
        analysis = analyze_soil(50, 20, 120, 6.5)
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
        analysis = analyze_soil(90, 80, 180, 8.5)
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
        self.assertAlmostEqual(by["N"]["deficit"], 10.0)
        self.assertAlmostEqual(by["P"]["deficit"], 5.0)
        self.assertAlmostEqual(by["K"]["deficit"], 60.0)
        self.assertAlmostEqual(by["ph"]["deficit"], 0.5)


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
        recs = recommend_soil(analyze_soil(50, 20, 120, 6.5))
        for rec in recs:
            self.assertNotIn("deficient", rec["status"])


class TestPrompt(unittest.TestCase):
    def test_prompt_contains_data_and_ranges(self):
        prompt = build_prompt(analyze_soil(25, 10, 40, 5.5))
        self.assertIn("Nitrogen", prompt)
        self.assertIn("deficient", prompt)
        self.assertIn("40-60", prompt)
        self.assertIn("15-30", prompt)
        self.assertIn("100-150", prompt)
        self.assertIn("6.0-7.0", prompt)


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


class TestNormalizeStatus(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_status("Low"), "deficient")
        self.assertEqual(normalize_status("HIGH"), "ample")
        self.assertEqual(normalize_status("optimal"), "adequate")
        self.assertEqual(normalize_status("not a status"), None)


if __name__ == "__main__":
    unittest.main()
