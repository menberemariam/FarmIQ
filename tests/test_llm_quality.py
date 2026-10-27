import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import evaluate_llm_quality as ev  # noqa: E402
from ML.engine import analyze_soil  # noqa: E402

ELEMENTS = ("Nitrogen", "Phosphorus", "Potassium", "pH")


class TestGoldenSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = ev._load_cases(ev.DEFAULT_CASES)
        cls.cases = cls.payload["cases"]

    def test_expected_size_and_mix(self):
        self.assertEqual(self.payload["purpose"], "evaluation_only")
        self.assertTrue(self.payload["not_training"])
        self.assertEqual(len(self.cases), 50)
        self.assertEqual(sum(len(c["expected"]["statuses"]) for c in self.cases), 200)
        self.assertEqual(sum(1 for c in self.cases if c["kind"] == "stress"), 5)
        self.assertEqual(sum(1 for c in self.cases if c["kind"] == "generated"), 45)
        self.assertGreaterEqual(
            sum(1 for c in self.cases if "edge" in c["tags"]), 7
        )
        self.assertEqual(
            len({case["id"] for case in self.cases}), len(self.cases)
        )

    def test_all_deterministic_cases_pass(self):
        results = [ev.evaluate_case(case) for case in self.cases]
        failures = [
            (result["id"], result["failures"])
            for result in results if not result["passed"]
        ]
        self.assertEqual(failures, [])

    def test_stress_cases_carry_advisory_assertions(self):
        by_id = {case["id"]: case for case in self.cases}
        self.assertIn("stress_acidic_nitisol_wheat", by_id)
        self.assertIn("stress_high_k_rift_onion", by_id)
        self.assertIn("stress_teff_lodging_boundary", by_id)
        self.assertIn("stress_vertisol_chickpea_legume_gap", by_id)
        self.assertIn("stress_alkaline_rift_maize", by_id)
        acid = by_id["stress_acidic_nitisol_wheat"]["expected"]
        self.assertIn("lime", [p.lower() for p in acid["must_include"]["pH"]])
        self.assertEqual(acid["numbers"]["pH"], [2.94])
        chickpea = by_id["stress_vertisol_chickpea_legume_gap"]
        self.assertEqual(chickpea["known_gaps"], ["legume_n_fixation"])

    def test_golden_set_has_no_training_fields(self):
        for case in self.cases:
            self.assertNotIn("label_status", case)
            self.assertNotIn("include_in_training", case)

    def test_loader_rejects_training_labels(self):
        payload = copy.deepcopy(self.payload)
        payload["cases"][0]["label_status"] = "Good"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                ev._load_cases(path)

    def test_loader_rejects_non_evaluation_set(self):
        payload = copy.deepcopy(self.payload)
        payload["purpose"] = "training"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                ev._load_cases(path)


class TestChecksAndMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = ev._load_cases(ev.DEFAULT_CASES)
        cls.case = cls.payload["cases"][0]

    def test_phrase_and_number_checks(self):
        case = {
            "expected": {
                "must_include": {"pH": ["lime"]},
                "must_not_include": {"Nitrogen": ["urea"]},
                "numbers": {"pH": [2.94]},
            }
        }
        recs = [
            {"element": "pH", "solution": "Apply lime at 2.94 t/ha."},
            {"element": "Nitrogen", "solution": "Use compost."},
        ]
        self.assertEqual(ev._check_phrases(case, recs), [])

        bad = [
            {"element": "pH", "solution": "Apply sulfur at 1.2 t/ha."},
            {"element": "Nitrogen", "solution": "Apply urea."},
        ]
        failures = ev._check_phrases(case, bad)
        self.assertTrue(any("missing required phrase" in f for f in failures))
        self.assertTrue(any("forbidden phrase" in f for f in failures))
        self.assertTrue(any("numeric citation" in f for f in failures))

    def test_status_contradiction_fails(self):
        case = copy.deepcopy(self.case)
        element = next(iter(case["expected"]["statuses"]))
        case["expected"]["statuses"][element] = "impossible"
        result = ev.evaluate_case(case)
        self.assertFalse(result["passed"])
        self.assertTrue(any("expected status" in f for f in result["failures"]))

    @mock.patch.object(ev, "get_llm_recommendations")
    def test_invalid_llm_output_falls_back_to_deterministic(self, mock_llm):
        mock_llm.return_value = {
            element: {"status": "contradiction", "solution": "junk"}
            for element in ELEMENTS
        }
        result = ev.evaluate_case(self.case, use_llm=True, api_key="test")
        self.assertTrue(result["passed"])
        self.assertTrue(result["llm"]["used"])
        self.assertEqual(len(result["llm"]["fact_failures"]), 4)

    def test_ngram_novelty(self):
        self.assertEqual(ev.ngram_novelty("a b c d", "a b c d"), 0.0)
        self.assertEqual(ev.ngram_novelty("x y z", "a b c"), 100.0)
        self.assertIsNone(ev.ngram_novelty("", "a b c"))

    def test_metric_density(self):
        self.assertGreater(ev.metric_density("apply 40 kg/ha now"), 0)
        self.assertEqual(ev.metric_density("apply it now"), 3.0)

    def test_flesch_kincaid_and_limitations(self):
        self.assertIsNotNone(ev.flesch_kincaid_grade("Apply urea at 40 kg/ha."))
        self.assertIsNone(ev.flesch_kincaid_grade(""))

    def test_evaluator_main_passes_deterministic(self):
        self.assertEqual(ev.main(["--cases", str(ev.DEFAULT_CASES)]), 0)

    def test_evaluator_main_rejects_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            self.assertEqual(ev.main(["--cases", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
