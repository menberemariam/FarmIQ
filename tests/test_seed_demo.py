import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import db
from tools import seed_demo


class TestSeedDemo(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "seed_test.db"

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def test_creates_demo_data(self):
        created, message = seed_demo.seed()
        self.assertTrue(created)
        self.assertIn("Seeded", message)

        farmers = db.list_farmer_summaries()
        self.assertEqual(len(farmers), 1)
        self.assertEqual(farmers[0]["name"], "Abebe Getachew")
        self.assertEqual(farmers[0]["region"], "Oromia-Arsi")
        self.assertEqual(farmers[0]["phone"], "0911234567")
        self.assertEqual(farmers[0]["plot_count"], 1)
        self.assertEqual(farmers[0]["report_count"], 2)

        plots = db.list_farmer_plots(farmers[0]["id"])
        self.assertEqual(len(plots), 1)
        plot = plots[0]
        self.assertEqual(plot["name"], "North Ridge")
        self.assertEqual(plot["farm_size_ha"], 2.1)
        self.assertEqual(plot["latitude"], 7.95)
        self.assertEqual(plot["longitude"], 39.13)
        self.assertEqual(plot["previous_crop"], "maize")
        self.assertEqual(plot["report_count"], 2)
        self.assertEqual(plot["latest_status"], "Good")

        # Newest first: the amended re-test (Good) then the original (Poor),
        # both backdated into a believable before/after timeline.
        reports = db.list_reports(farmer_id=farmers[0]["id"])
        self.assertEqual(len(reports), 2)
        newer, older = reports
        self.assertEqual(newer["created_at"][:10], "2026-09-15")
        self.assertEqual(newer["fertility_status"], "Good")
        self.assertEqual(older["created_at"][:10], "2026-05-10")
        self.assertEqual(older["fertility_status"], "Poor")
        for report in reports:
            self.assertEqual(report["farmer_id"], farmers[0]["id"])
            self.assertEqual(report["plot_id"], plot["id"])
            self.assertEqual(report["source"], "manual")
            self.assertEqual(report["crop"], "maize")
            self.assertTrue(report["recommendations"])
            self.assertEqual(report["recommendations"][0]["source"], "rule-based")

        # Outcome hangs off the first (Poor) test.
        outcome = db.get_outcome(older["id"])
        self.assertIsNotNone(outcome)
        self.assertIsNone(db.get_outcome(newer["id"]))
        self.assertEqual(outcome["expected_yield"], 3500.0)
        self.assertEqual(outcome["actual_yield"], 3200.0)
        self.assertEqual(outcome["yield_unit"], "kg/ha")
        self.assertEqual(outcome["harvest_at"], "2026-09-10")
        self.assertIn("Lime", outcome["implementation_notes"])

        # Demo device is bound to the demo plot with the documented token.
        device = db.get_device(seed_demo.DEMO_DEVICE_ID)
        self.assertIsNotNone(device)
        self.assertEqual(device["name"], "Oromia Soil Prototype 01")
        self.assertEqual(device["token"], "soil001-demo-token")
        self.assertEqual(device["plot_id"], plot["id"])
        self.assertEqual(device["battery"], 87.0)

    def test_idempotent_without_force(self):
        seed_demo.seed()
        created, message = seed_demo.seed()
        self.assertFalse(created)
        self.assertIn("already exists", message)
        self.assertIn("--force", message)

        self.assertEqual(len(db.list_farmer_summaries()), 1)
        self.assertEqual(len(db.list_reports()), 2)
        # the device stays single too
        self.assertEqual(len(db.list_devices()), 1)

    def test_force_wipes_device_readings_and_reseeds(self):
        seed_demo.seed()
        device = db.get_device(seed_demo.DEMO_DEVICE_ID)
        db.insert_reading(
            device["id"], "iot_simulator", 55, 25, 110, 6.5, 45, 22,
            "valid", [], "2026-09-27T12:00:00+00:00",
        )

        created, message = seed_demo.seed(force=True)
        self.assertTrue(created)
        self.assertIn(seed_demo.DEMO_DEVICE_ID, message)

        reseeded = db.get_device(seed_demo.DEMO_DEVICE_ID)
        self.assertIsNotNone(reseeded)
        self.assertNotEqual(reseeded["id"], device["id"])  # fresh row
        self.assertEqual(len(db.list_readings(reseeded["id"])), 0)
        self.assertEqual(len(db.list_devices()), 1)

    def test_force_wipes_and_reseeds(self):
        seed_demo.seed()
        rogue_id = db.create_farmer("Rogue Farmer")
        db.save_report("Rogue Plot", 40, 20, 80, 6.5, "Moderate",
                       [{"element": "Nitrogen", "status": "adequate",
                         "deficit": 0.0, "solution": "—", "source": "rule-based"}],
                       farmer_id=rogue_id)

        created, message = seed_demo.seed(force=True)
        self.assertTrue(created)
        self.assertIn("Seeded", message)

        # Demo farmer's data was wiped and rebuilt exactly once; the
        # unrelated farmer is untouched.
        farmers = [f for f in db.list_farmer_summaries() if f["name"] == "Abebe Getachew"]
        self.assertEqual(len(farmers), 1)
        self.assertEqual(farmers[0]["report_count"], 2)
        reports = db.list_reports(farmer_id=farmers[0]["id"])
        self.assertEqual({r["fertility_status"] for r in reports}, {"Poor", "Good"})
        self.assertEqual(len(db.list_farmer_summaries()), 2)

    def test_cli_writes_to_given_db_and_skips_second_run(self):
        target = Path(self.tmpdir.name) / "cli.db"

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = seed_demo.main(["--db", str(target)])
        self.assertEqual(code, 0)
        self.assertIn("Seeded:", out.getvalue())
        self.assertTrue(target.exists())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = seed_demo.main(["--db", str(target)])
        self.assertEqual(code, 0)
        self.assertIn("Skipped:", out.getvalue())

        # --db redirects config.DB_PATH in this process, so the demo data
        # is reachable through the normal db helpers.
        self.assertEqual(config.DB_PATH, target)
        self.assertEqual(len(db.list_reports()), 2)


if __name__ == "__main__":
    unittest.main()
