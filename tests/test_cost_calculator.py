"""Unit tests for the Phase 4 fertilizer cost estimator (fertilizer_cost.py)."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import db
import fertilizer_cost
from ML.engine import analyze_soil, recommend_soil

# Sample soil values against the generic bands (config.IDEAL_RANGES,
# EthioSIS profile: N 60-100, P 78-117 (lab 30-45 mg/kg x 2.6),
# K 78.2-156.4, pH 6.5-7.5).
ALL_ADEQUATE = {"N": 80.0, "P": 90.0, "K": 120.0, "ph": 6.8}
DEFICIENT_N = {"N": 20.0, "P": 90.0, "K": 120.0, "ph": 6.8}
DEFICIENT_P = {"N": 80.0, "P": 10.0, "K": 120.0, "ph": 6.8}
DEFICIENT_K = {"N": 80.0, "P": 90.0, "K": 60.0, "ph": 6.8}
ACIDIC = {"N": 80.0, "P": 90.0, "K": 120.0, "ph": 4.8}
ALKALINE = {"N": 80.0, "P": 90.0, "K": 120.0, "ph": 8.0}


def line_for(est, element):
    return next(line for line in est["lines"] if line["element"] == element)


def solution_for(values, element, crop=None, lang="en"):
    analysis = analyze_soil(values["N"], values["P"], values["K"],
                            values["ph"], crop=crop)
    recs = recommend_soil(analysis, lang=lang)
    return recs[[r["element"] for r in recs].index(element)]["solution"]


class TestPriceSeed(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "cost_test.db"

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def test_default_prices_seeded_and_idempotent(self):
        db.init_db()
        rows = db.list_fertilizer_prices()
        self.assertEqual(
            {r["product"] for r in rows},
            {"urea", "dap", "potassium_sulfate", "lime", "sulfur"},
        )
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertGreater(row["price_etb_per_kg"], 0)
            self.assertIn("Indicative demo price", row["note"])
        # Re-running init_db must not duplicate or overwrite rows.
        db.init_db()
        db.init_db()
        self.assertEqual(db.list_fertilizer_prices(), rows)

    def test_existing_price_edits_survive_reboot(self):
        db.init_db()
        conn = db._connect()
        conn.execute(
            "UPDATE fertilizer_prices SET price_etb_per_kg = ? WHERE product = 'urea'",
            (99.0,),
        )
        conn.commit()
        conn.close()
        db.init_db()
        urea = next(r for r in db.list_fertilizer_prices()
                    if r["product"] == "urea")
        self.assertEqual(urea["price_etb_per_kg"], 99.0)


class TestEstimate(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "cost_test.db"
        db.init_db()
        self.prices = {r["product"]: r["price_etb_per_kg"]
                       for r in db.list_fertilizer_prices()}

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def test_all_adequate_costs_nothing(self):
        est = fertilizer_cost.estimate(**ALL_ADEQUATE)
        self.assertEqual(est["currency"], "ETB")
        self.assertEqual(len(est["lines"]), 4)
        for line in est["lines"]:
            self.assertFalse(line["purchase"])
            self.assertEqual(line["amount"], 0.0)
            self.assertEqual(line["subtotal"], 0.0)
        self.assertEqual(est["total_per_ha"], 0.0)
        self.assertIsNone(est["total_for_plot"])
        self.assertIn("Indicative", est["disclaimer"])
        self.assertEqual(est["disclaimer_key"], "costDisclaimer")

    def test_deficient_n_costs_urea_matching_solution_text(self):
        est = fertilizer_cost.estimate(**DEFICIENT_N)
        line = line_for(est, "Nitrogen")
        self.assertTrue(line["purchase"])
        self.assertEqual(line["product"], "urea")
        self.assertEqual(line["unit"], "kg/ha")
        # Cross-check: amount must equal the kg/ha printed in the report text.
        match = re.search(r"urea \(46% N\) at approximately ([\d.]+) kg/ha",
                          solution_for(DEFICIENT_N, "Nitrogen"))
        self.assertIsNotNone(match)
        self.assertEqual(line["amount"], float(match.group(1)))
        self.assertEqual(line["price_etb_per_kg"], self.prices["urea"])
        self.assertEqual(line["subtotal"],
                         round(line["amount"] * self.prices["urea"], 2))

    def test_deficient_p_costs_dap_matching_solution_text(self):
        est = fertilizer_cost.estimate(**DEFICIENT_P)
        line = line_for(est, "Phosphorus")
        self.assertEqual(line["product"], "dap")
        match = re.search(r"at approximately ([\d.]+) kg/ha",
                          solution_for(DEFICIENT_P, "Phosphorus"))
        self.assertEqual(line["amount"], float(match.group(1)))
        self.assertEqual(line["subtotal"],
                         round(line["amount"] * self.prices["dap"], 2))

    def test_deficient_k_costs_potassium_sulfate_matching_solution_text(self):
        est = fertilizer_cost.estimate(**DEFICIENT_K)
        line = line_for(est, "Potassium")
        self.assertEqual(line["product"], "potassium_sulfate")
        match = re.search(
            r"potassium sulfate \(50% K2O\) at approximately ([\d.]+) kg/ha",
            solution_for(DEFICIENT_K, "Potassium"))
        self.assertEqual(line["amount"], float(match.group(1)))
        self.assertEqual(line["subtotal"],
                         round(line["amount"] * self.prices["potassium_sulfate"], 2))

    def test_acidic_soil_costs_lime_in_tonnes(self):
        est = fertilizer_cost.estimate(**ACIDIC)
        line = line_for(est, "pH")
        self.assertTrue(line["purchase"])
        self.assertEqual(line["product"], "lime")
        self.assertEqual(line["unit"], "t/ha")
        match = re.search(r"lime \(CaCO3\) at roughly ([\d.]+) t/ha",
                          solution_for(ACIDIC, "pH"))
        self.assertEqual(line["amount"], float(match.group(1)))
        # Tonnes convert to kg before pricing.
        self.assertEqual(line["subtotal"],
                         round(line["amount"] * 1000 * self.prices["lime"], 2))

    def test_alkaline_soil_costs_sulfur_in_tonnes(self):
        est = fertilizer_cost.estimate(**ALKALINE)
        line = line_for(est, "pH")
        self.assertTrue(line["purchase"])
        self.assertEqual(line["product"], "sulfur")
        self.assertEqual(line["unit"], "t/ha")
        match = re.search(r"sulfur at approximately ([\d.]+) t/ha",
                          solution_for(ALKALINE, "pH"))
        self.assertEqual(line["amount"], float(match.group(1)))
        self.assertEqual(line["subtotal"],
                         round(line["amount"] * 1000 * self.prices["sulfur"], 2))

    def test_totals_equal_sum_of_lines_and_respect_plot_size(self):
        est = fertilizer_cost.estimate(N=20.0, P=10.0, K=120.0, ph=4.8,
                                       size_ha=2.1)
        self.assertGreater(est["total_per_ha"], 0)
        self.assertEqual(
            est["total_per_ha"],
            round(sum(l["subtotal"] for l in est["lines"]), 2),
        )
        self.assertEqual(est["size_ha"], 2.1)
        self.assertEqual(est["total_for_plot"],
                         round(est["total_per_ha"] * 2.1, 2))

    def test_crop_specific_bands_change_the_cost(self):
        # Same sample: the generic N band (60-100, EthioSIS) is satisfied at
        # N=80, while banana's dataset band (95.5-105.5) makes nitrogen
        # deficient — cost appears only for the crop-specific view.
        generic = fertilizer_cost.estimate(N=80.0, P=90.0, K=120.0, ph=6.8)
        banana = fertilizer_cost.estimate(N=80.0, P=90.0, K=120.0, ph=6.8,
                                          crop="banana")
        self.assertFalse(line_for(generic, "Nitrogen")["purchase"])
        line = line_for(banana, "Nitrogen")
        self.assertTrue(line["purchase"])
        self.assertEqual(line["product"], "urea")
        self.assertGreater(line["subtotal"], 0)

    def test_missing_price_row_yields_zero_not_crash(self):
        conn = db._connect()
        conn.execute("DELETE FROM fertilizer_prices WHERE product = 'urea'")
        conn.commit()
        conn.close()
        est = fertilizer_cost.estimate(**DEFICIENT_N)
        line = line_for(est, "Nitrogen")
        self.assertEqual(line["product"], "urea")
        self.assertEqual(line["price_etb_per_kg"], 0.0)
        self.assertEqual(line["subtotal"], 0.0)


if __name__ == "__main__":
    unittest.main()
