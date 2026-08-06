import sys
import tempfile
import unittest
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import db

SAMPLE_RECS = [
    {"element": "Nitrogen", "status": "deficient", "deficit": 10.0, "solution": "Apply urea.", "source": "rule-based"}
]
SAMPLE_CROPS = [{"crop": "rice", "confidence": 0.9}]


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "test_reports.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        self.tmpdir.cleanup()

    def test_save_and_list_round_trip(self):
        rid = db.save_report(
            "North Field", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS,
            crop="rice", temperature=25.0, humidity=80.0, rainfall=200.0,
            crops=SAMPLE_CROPS,
        )
        self.assertIsInstance(rid, int)
        reports = db.list_reports(plot="North Field")
        self.assertEqual(len(reports), 1)
        r = reports[0]
        self.assertEqual(r["N"], 25)
        self.assertEqual(r["fertility_status"], "Poor")
        self.assertEqual(r["crop"], "rice")
        self.assertEqual(r["recommendations"][0]["element"], "Nitrogen")
        self.assertEqual(r["crops"][0]["crop"], "rice")

    def test_plots_grouping(self):
        db.save_report("A", 10, 10, 10, 6.0, "Poor", SAMPLE_RECS)
        db.save_report("B", 50, 20, 120, 6.5, "Good", SAMPLE_RECS)
        db.save_report("A", 20, 15, 60, 6.2, "Moderate", SAMPLE_RECS)
        plots = {p["plot"]: p["count"] for p in db.list_plots()}
        self.assertEqual(plots, {"A": 2, "B": 1})

    def test_latest_two_deltas_are_real(self):
        db.save_report("Field", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS)
        db.save_report("Field", 45, 18, 90, 6.4, "Moderate", SAMPLE_RECS)
        tracking = db.latest_two("Field")
        self.assertEqual(tracking["deltas"], {"N": 20.0, "P": 8.0, "K": 50.0, "ph": 0.9})
        self.assertTrue(tracking["status_changed"])
        self.assertEqual((tracking["old_status"], tracking["new_status"]), ("Poor", "Moderate"))

    def test_latest_two_requires_two_reports(self):
        db.save_report("Field", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS)
        self.assertIsNone(db.latest_two("Field"))

    def test_empty_history(self):
        self.assertEqual(db.list_plots(), [])
        self.assertEqual(db.list_reports(), [])


if __name__ == "__main__":
    unittest.main()
