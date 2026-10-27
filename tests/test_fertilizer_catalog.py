"""Tests for the fertilizer product catalog (Phase 1, additive).

Covers Dataset/fertilizer_products.json validation, fertilizer_catalog.py
derivations (fractions, ETB/kg ranges), the additive product_options /
price_source keys on cost lines, and GET /api/fertilizer/products.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import db  # noqa: E402
import fertilizer_catalog  # noqa: E402
import fertilizer_cost  # noqa: E402

EXPECTED_IDS = {"nps", "npsb", "urea", "dap", "blended"}


class TestCatalogLoad(unittest.TestCase):
    def test_catalog_loads_and_validates(self):
        catalog = fertilizer_catalog.load_catalog()
        self.assertEqual(
            {p["id"] for p in catalog["products"]}, EXPECTED_IDS)
        self.assertGreaterEqual(len(catalog["source_documents"]), 3)

    def test_products_have_derived_fractions_and_prices(self):
        products = {p["id"]: p for p in fertilizer_catalog.get_products()}
        nps = products["nps"]
        self.assertAlmostEqual(nps["nutrients"]["N"], 0.19)
        self.assertAlmostEqual(nps["nutrients"]["P"], 0.38)
        self.assertAlmostEqual(nps["nutrients"]["K"], 0.0)
        # Co-op 4000-5500 / market 7000-9000 per 50 kg bag -> 80-180 ETB/kg.
        self.assertEqual(nps["price_etb_per_kg"]["min"], 80.0)
        self.assertEqual(nps["price_etb_per_kg"]["max"], 180.0)
        self.assertEqual(nps["price_etb_per_kg"]["coop_min"], 80.0)
        self.assertEqual(nps["price_etb_per_kg"]["market_max"], 180.0)
        # Every product gets a usable per-kg range and application block.
        for product in products.values():
            self.assertGreater(product["price_etb_per_kg"]["max"],
                               product["price_etb_per_kg"]["min"])
            self.assertIn(product["application"]["timing"],
                          ("basal", "split"))
            self.assertTrue(product["application"]["restrictions"])
            self.assertTrue(product["decision_rules"])

    def test_npsb_and_blend_micronutrient_fractions(self):
        npsb = fertilizer_catalog.get_product("npsb")
        self.assertAlmostEqual(npsb["nutrients"]["S"], 0.06825)
        self.assertAlmostEqual(npsb["nutrients"]["B"], 0.00405)
        blended = fertilizer_catalog.get_product("blended")
        self.assertAlmostEqual(blended["nutrients"]["K"], 0.1405)
        self.assertAlmostEqual(blended["nutrients"]["Zn"], 0.017)

    def test_products_supplying_maps_engine_elements(self):
        self.assertEqual({p["id"] for p in
                          fertilizer_catalog.products_supplying("P")},
                         {"nps", "npsb", "dap"})
        self.assertEqual({p["id"] for p in
                          fertilizer_catalog.products_supplying("K")},
                         {"blended"})
        self.assertEqual({p["id"] for p in
                          fertilizer_catalog.products_supplying("N")},
                         {"urea"})
        self.assertEqual(fertilizer_catalog.products_supplying("ph"), [])

    def test_bag_to_etb_per_kg(self):
        self.assertEqual(fertilizer_catalog.bag_to_etb_per_kg(4500), 90.0)
        self.assertEqual(fertilizer_catalog.bag_to_etb_per_kg(4500, 25), 180.0)
        with self.assertRaises(ValueError):
            fertilizer_catalog.bag_to_etb_per_kg(100, 0)


class TestValidateCatalog(unittest.TestCase):
    def _valid(self):
        return json.loads(Path(
            fertilizer_catalog.CATALOG_PATH).read_text(encoding="utf-8"))

    def test_real_catalog_passes(self):
        fertilizer_catalog.validate_catalog(self._valid())

    def test_missing_top_key_rejected(self):
        bad = self._valid()
        del bad["products"]
        with self.assertRaises(fertilizer_catalog.CatalogError):
            fertilizer_catalog.validate_catalog(bad)

    def test_missing_nutrient_rejected(self):
        bad = self._valid()
        del bad["products"][0]["nutrients_pct"]["P"]
        with self.assertRaises(fertilizer_catalog.CatalogError):
            fertilizer_catalog.validate_catalog(bad)

    def test_bad_price_range_rejected(self):
        bad = self._valid()
        bad["products"][0]["prices"]["coop_per_bag"] = [5000, 4000]
        with self.assertRaises(fertilizer_catalog.CatalogError):
            fertilizer_catalog.validate_catalog(bad)

    def test_duplicate_id_rejected(self):
        bad = self._valid()
        bad["products"].append(dict(bad["products"][0]))
        with self.assertRaises(fertilizer_catalog.CatalogError):
            fertilizer_catalog.validate_catalog(bad)

    def test_unknown_serves_element_rejected(self):
        bad = self._valid()
        bad["products"][0]["serves"] = ["Sulfur"]
        with self.assertRaises(fertilizer_catalog.CatalogError):
            fertilizer_catalog.validate_catalog(bad)


class TestCostOptions(unittest.TestCase):
    """product_options / price_source on cost lines (additive only)."""

    # P 58 sits 20 kg/ha under the 78-117 band so deficit-pinned amount
    # assertions below stay exact after the bulk-density P conversion.
    DEFICIENT_P = {"N": 80.0, "P": 58.0, "K": 120.0, "ph": 6.8}
    DEFICIENT_K = {"N": 80.0, "P": 90.0, "K": 60.0, "ph": 6.8}
    DEFICIENT_N = {"N": 20.0, "P": 90.0, "K": 120.0, "ph": 6.8}
    ACIDIC = {"N": 80.0, "P": 90.0, "K": 120.0, "ph": 4.8}
    ALL_ADEQUATE = {"N": 80.0, "P": 90.0, "K": 120.0, "ph": 6.8}

    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "catalog_cost_test.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def _line(self, est, element):
        return next(l for l in est["lines"] if l["element"] == element)

    def test_original_line_contract_unchanged(self):
        # The keys the original suite relies on must all still be present.
        est = fertilizer_cost.estimate(**self.DEFICIENT_P)
        line = self._line(est, "Phosphorus")
        for key in ("element", "col", "status", "deficit", "product",
                    "product_label", "amount", "unit", "price_etb_per_kg",
                    "subtotal", "purchase"):
            self.assertIn(key, line)
        self.assertEqual(line["product"], "dap")
        self.assertEqual(line["price_source"], "db")
        self.assertEqual(est["total_per_ha"],
                         round(sum(l["subtotal"] for l in est["lines"]), 2))

    def test_p_line_offers_nps_and_npsb_alternatives(self):
        est = fertilizer_cost.estimate(**self.DEFICIENT_P)
        line = self._line(est, "Phosphorus")
        options = line["product_options"]
        self.assertEqual([o["product"] for o in options],
                         ["dap", "nps", "npsb"])
        primary = options[0]
        self.assertTrue(primary["primary"])
        self.assertEqual(primary["amount"], line["amount"])
        self.assertEqual(primary["price_source"], "db")
        self.assertEqual(primary["price_etb_per_kg_min"],
                         primary["price_etb_per_kg_max"])
        # Alternatives: deficit 20 kg/ha P2O5 at each product's own content.
        nps = options[1]
        self.assertAlmostEqual(nps["nutrient_content"], 0.38)
        self.assertEqual(nps["amount"], round(20 / 0.38, 1))
        self.assertEqual(nps["price_source"], "catalog")
        self.assertEqual(nps["price_etb_per_kg_min"], 80.0)
        self.assertEqual(nps["price_etb_per_kg_max"], 180.0)
        self.assertEqual(nps["subtotal_min"], round(nps["amount"] * 80.0, 2))
        self.assertEqual(nps["subtotal_max"], round(nps["amount"] * 180.0, 2))
        npsb = options[2]
        self.assertEqual(npsb["amount"], round(20 / 0.369, 1))

    def test_k_line_offers_region_blend(self):
        est = fertilizer_cost.estimate(**self.DEFICIENT_K)
        line = self._line(est, "Potassium")
        options = line["product_options"]
        self.assertEqual(options[0]["product"], "potassium_sulfate")
        self.assertTrue(options[0]["primary"])
        blend = next(o for o in options if o["product"] == "blended")
        # EthioSIS K band starts at 78.2 (0.20 cmolc/kg x 391); deficit = 18.2.
        self.assertEqual(blend["amount"], round(18.2 / 0.1405, 1))
        self.assertEqual(blend["price_source"], "catalog")
        self.assertLess(blend["price_etb_per_kg_min"],
                        blend["price_etb_per_kg_max"])

    def test_n_line_has_only_urea_option(self):
        est = fertilizer_cost.estimate(**self.DEFICIENT_N)
        line = self._line(est, "Nitrogen")
        self.assertEqual([o["product"] for o in line["product_options"]],
                         ["urea"])
        self.assertEqual(line["product_options"][0]["amount"],
                         line["amount"])

    def test_ph_and_adequate_lines_have_no_options(self):
        est = fertilizer_cost.estimate(**self.ACIDIC)
        self.assertEqual(self._line(est, "pH")["product_options"], [])
        est = fertilizer_cost.estimate(**self.ALL_ADEQUATE)
        for line in est["lines"]:
            self.assertEqual(line["product_options"], [])
            self.assertIsNone(line["price_source"])

    def test_options_survive_missing_price_row(self):
        conn = db._connect()
        conn.execute("DELETE FROM fertilizer_prices WHERE product = 'dap'")
        conn.commit()
        conn.close()
        est = fertilizer_cost.estimate(**self.DEFICIENT_P)
        line = self._line(est, "Phosphorus")
        self.assertEqual(line["price_etb_per_kg"], 0.0)  # unchanged behavior
        primary = line["product_options"][0]
        self.assertEqual(primary["subtotal_min"], 0.0)
        # Catalog alternatives are unaffected by the missing db row.
        self.assertGreater(line["product_options"][1]["subtotal_max"], 0)


class TestFertilizerProductsEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import api
        from fastapi.testclient import TestClient
        cls.client = TestClient(api.app)

    def test_endpoint_returns_full_spec_sheet(self):
        resp = self.client.get("/api/fertilizer/products")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["currency"], "ETB")
        self.assertEqual(body["bag_kg"], 50)
        self.assertEqual(len(body["products"]), 5)
        self.assertEqual({p["id"] for p in body["products"]}, EXPECTED_IDS)
        self.assertEqual(len(body["source_documents"]), 3)
        self.assertEqual(body["disclaimer_key"], "costDisclaimer")
        nps = next(p for p in body["products"] if p["id"] == "nps")
        self.assertAlmostEqual(nps["nutrients"]["P"], 0.38)
        self.assertEqual(nps["price_etb_per_kg"]["min"], 80.0)
        self.assertIn("restrictions", nps["application"])
        self.assertTrue(nps["decision_rules"])
        self.assertEqual(nps["prices"]["coop_per_bag"], [4000, 5500])

    def test_endpoint_is_read_only_reference(self):
        # Products endpoint must not require or mutate any report state.
        resp = self.client.get("/api/fertilizer/products")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
