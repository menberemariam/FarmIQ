"""Enhancement D: Ethiopian Meher/Belg seasonal advisory nitrogen bands."""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

_tmpdir = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmpdir.name) / "test_seasonal_bands.db"

import api  # noqa: E402
import db  # noqa: E402
import fertilizer_cost  # noqa: E402
import seasonal_bands  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ML.engine import analyze_soil, get_bands  # noqa: E402

client = TestClient(api.app)


class TestSeasonalData(unittest.TestCase):
    def test_dataset_has_six_crops_twelve_entries(self):
        self.assertEqual(
            seasonal_bands.season_crops(),
            ["faba_bean", "maize", "potato", "sorghum", "teff", "wheat"],
        )
        self.assertEqual(seasonal_bands.list_seasons(), ["belg", "meher"])
        entries = [
            seasonal_bands.resolve_seasonal_bands(crop, season)
            for crop in seasonal_bands.season_crops()
            for season in seasonal_bands.list_seasons()
        ]
        self.assertEqual(len(entries), 12)
        self.assertTrue(all(e["review_status"] == "unverified" for e in entries))
        self.assertEqual(
            seasonal_bands.meta()["review_status"], "unverified")
        self.assertIn("not be used as supervised training",
                      seasonal_bands.meta()["description"])

    def test_crop_and_season_aliases(self):
        self.assertEqual(seasonal_bands.canonical_crop("Corn"), "maize")
        self.assertEqual(seasonal_bands.canonical_crop("Faba Bean"), "faba_bean")
        self.assertEqual(seasonal_bands.canonical_crop("tef"), "teff")
        self.assertEqual(seasonal_bands.canonical_season("Kiremt"), "meher")
        self.assertEqual(seasonal_bands.canonical_season("short rains"), "belg")
        self.assertIsNone(seasonal_bands.canonical_crop("coffee"))
        self.assertIsNone(seasonal_bands.canonical_season("dry"))

    def test_resolve_returns_seasonal_n_and_static_baseline(self):
        entry = seasonal_bands.resolve_seasonal_bands("maize", "belg")
        self.assertEqual(entry["id"], "maize_belg")
        self.assertEqual(entry["bands"]["N"], [100.0, 140.0])
        self.assertEqual(entry["bands"]["P"], [45.0, 90.0])
        self.assertEqual(entry["base_bands"]["K"], [300.0, 450.0])
        self.assertEqual(entry["n_factor"], 0.5714)
        self.assertIsNone(
            seasonal_bands.resolve_seasonal_bands("coffee", "meher"))


class TestSeasonalEngine(unittest.TestCase):
    def test_no_season_is_byte_identical(self):
        for crop in ("maize", "potato", "coffee", "generic", None):
            self.assertEqual(
                get_bands(crop),
                get_bands(crop, season=None),
            )

    def test_crop_season_uses_seasonal_ethiopian_baseline(self):
        meher = get_bands("maize", season="meher")
        self.assertEqual(meher["N"], [160.0, 260.0])
        self.assertEqual(meher["P"], [45.0, 90.0])
        self.assertEqual(meher["K"], [300.0, 450.0])
        self.assertEqual(meher["ph"], [5.8, 7.0])
        belg = get_bands("maize", season="belg")
        self.assertEqual(belg["N"], [100.0, 140.0])
        self.assertEqual(belg["P"], [45.0, 90.0])

    def test_variety_preserves_pk_ph_and_scales_belg_n(self):
        meher = get_bands(
            "maize", variety="BH-660", region="Oromia", season="meher")
        self.assertEqual(meher["N"], [160.0, 240.0])
        belg = get_bands(
            "maize", variety="BH-660", region="Oromia", season="belg")
        self.assertEqual(belg["P"], [45.0, 90.0])
        self.assertEqual(belg["K"], [300.0, 450.0])
        self.assertEqual(belg["ph"], [5.5, 6.8])
        self.assertEqual(belg["N"], [91.4, 137.1])

    def test_zone_preserves_pk_ph_and_scales_belg_n(self):
        base = get_bands("maize", zone="highland_nitisols")
        meher = get_bands(
            "maize", zone="highland_nitisols", season="meher")
        belg = get_bands(
            "maize", zone="highland_nitisols", season="belg")
        self.assertEqual(
            {col: list(band) for col, band in meher.items()},
            {col: list(band) for col, band in base.items()},
        )
        self.assertEqual(belg["P"], list(base["P"]))
        self.assertEqual(belg["K"], list(base["K"]))
        self.assertEqual(belg["ph"], list(base["ph"]))
        self.assertEqual(belg["N"], [round(base["N"][0] * 0.5714, 1),
                                     round(base["N"][1] * 0.5714, 1)])

    def test_analysis_marks_season_and_keeps_pk_sources(self):
        analysis = analyze_soil(
            100, 30, 250, 6.0, crop="maize", season="belg")
        by_col = {item["col"]: item for item in analysis}
        self.assertEqual(by_col["N"]["bands_source"], "season")
        self.assertEqual(by_col["N"]["season"], "belg")
        self.assertEqual(by_col["N"]["season_applied_n_source"], "season_table")
        self.assertEqual(by_col["P"]["season_applied_n_source"], "season_table")
        self.assertEqual(tuple(by_col["N"]["band"]), (100.0, 140.0))

    def test_variety_analysis_marks_scaled_n(self):
        analysis = analyze_soil(
            100, 30, 250, 6.0, crop="maize", region="Oromia",
            variety="BH-660", season="belg")
        n = next(item for item in analysis if item["col"] == "N")
        self.assertEqual(n["bands_source"], "variety")
        self.assertEqual(n["season_applied_n_source"], "variety_x_belg_factor")
        self.assertEqual(n["season_n_factor"], 0.5714)
        self.assertEqual(tuple(n["band"]), (91.4, 137.1))


class TestSeasonalApi(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_season_api.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def test_api_lists_seasons_and_crops(self):
        resp = client.get("/api/seasons")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual({s["id"] for s in body["seasons"]}, {"meher", "belg"})
        self.assertEqual(len(body["crops"]), 6)
        self.assertEqual(len(body["entries"]), 12)
        maize = client.get(
            "/api/seasons", params={"crop": "corn"}).json()["entries"]
        self.assertEqual({e["season"] for e in maize}, {"meher", "belg"})

    def test_predict_maize_belg_returns_season_block(self):
        resp = client.post("/api/predict", json={
            "N": 100, "P": 30, "K": 250, "ph": 6.0,
            "crop": "maize", "season": "belg",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["season"]["id"], "maize_belg")
        self.assertEqual(body["season"]["bands"]["N"], [100.0, 140.0])
        self.assertEqual(body["season"]["unchanged"], ["P", "K", "ph"])
        n = next(r for r in body["recommendations"] if r["element"] == "Nitrogen")
        self.assertEqual(n["ideal_range"], "100-140 kg/ha")
        self.assertIn("Belg", n["solution"])

    def test_predict_non_ml_crop_with_season(self):
        resp = client.post("/api/predict", json={
            "N": 100, "P": 30, "K": 250, "ph": 6.0,
            "crop": "wheat", "season": "meher",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["season"]["crop"], "wheat")
        self.assertEqual(body["season"]["bands"]["N"], [140.0, 220.0])

    def test_predict_rejects_unknown_and_unsupported_seasons(self):
        base = {"N": 100, "P": 30, "K": 250, "ph": 6.0}
        self.assertEqual(client.post("/api/predict", json={
            **base, "crop": "maize", "season": "dry"}).status_code, 400)
        self.assertEqual(client.post("/api/predict", json={
            **base, "season": "meher"}).status_code, 400)
        self.assertEqual(client.post("/api/predict", json={
            **base, "crop": "coffee", "season": "meher"}).status_code, 400)

    def test_no_season_has_no_season_block(self):
        resp = client.post("/api/predict", json={
            "N": 100, "P": 30, "K": 250, "ph": 6.0, "crop": "maize",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("season", resp.json())

    def test_save_and_cost_keep_season(self):
        resp = client.post("/api/reports", json={
            "plot": "Belg Field", "N": 100, "P": 30, "K": 250, "ph": 6.0,
            "fertility_status": "Poor", "recommendations": [],
            "crop": "maize", "season": "belg",
        })
        self.assertEqual(resp.status_code, 200)
        report_id = resp.json()["id"]
        detail = client.get(f"/api/reports/{report_id}").json()["report"]
        self.assertEqual(detail["season"], "belg")

        cost = client.get(f"/api/reports/{report_id}/cost-estimate").json()
        direct = fertilizer_cost.estimate(
            100, 30, 250, 6.0, crop="maize", season="belg")
        self.assertEqual(cost["total_per_ha"], direct["total_per_ha"])

    def test_save_rejects_unsupported_season(self):
        resp = client.post("/api/reports", json={
            "plot": "Bad Season", "N": 100, "P": 30, "K": 250, "ph": 6.0,
            "fertility_status": "Poor", "recommendations": [],
            "crop": "coffee", "season": "meher",
        })
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
