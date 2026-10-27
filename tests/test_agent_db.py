import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import db

SAMPLE_RECS = [
    {"element": "Nitrogen", "status": "deficient", "deficit": 10.0,
     "solution": "Apply urea.", "source": "rule-based"}
]
SAMPLE_CROPS = [{"crop": "rice", "confidence": 0.9}]


class AgentDbTestCase(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "agent_test.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()


class TestFarmerPlotCrud(AgentDbTestCase):
    def test_farmer_round_trip(self):
        fid = db.create_farmer("Abebe Getachew", phone="0911...", region="Oromia", lang="am")
        farmer = db.get_farmer(fid)
        self.assertEqual(farmer["name"], "Abebe Getachew")
        self.assertEqual(farmer["region"], "Oromia")
        self.assertEqual(farmer["lang"], "am")
        self.assertIsNone(db.get_farmer(9999))

    def test_farmer_summaries_counts(self):
        self.assertEqual(db.list_farmer_summaries(), [])
        fid = db.create_farmer("Abebe")
        db.create_plot(fid, "North Ridge", farm_size_ha=2.1, latitude=7.95, longitude=39.13)
        db.save_report("North Ridge", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS, farmer_id=fid)
        summaries = db.list_farmer_summaries()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["plot_count"], 1)
        self.assertEqual(summaries[0]["report_count"], 1)
        self.assertIsNotNone(summaries[0]["last_report_at"])

    def test_plots_for_farmer_with_latest_status(self):
        fid = db.create_farmer("Abebe")
        pid = db.create_plot(fid, "North Ridge", farm_size_ha=2.1, previous_crop="maize")
        db.save_report("North Ridge", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS,
                       farmer_id=fid, plot_id=pid)
        plots = db.list_farmer_plots(fid)
        self.assertEqual(len(plots), 1)
        self.assertEqual(plots[0]["name"], "North Ridge")
        self.assertEqual(plots[0]["report_count"], 1)
        self.assertEqual(plots[0]["latest_status"], "Poor")
        self.assertEqual(db.get_plot(pid)["farmer_id"], fid)
        self.assertIsNone(db.get_plot(9999))


class TestReportAgentFields(AgentDbTestCase):
    def test_save_report_with_agent_fields(self):
        fid = db.create_farmer("Abebe")
        pid = db.create_plot(fid, "North Ridge")
        rid = db.save_report("North Ridge", 45, 18, 90, 6.4, "Moderate", SAMPLE_RECS,
                             crop="rice", farmer_id=fid, plot_id=pid, source="manual")
        report = db.get_report(rid)
        self.assertEqual(report["farmer_id"], fid)
        self.assertEqual(report["plot_id"], pid)
        self.assertEqual(report["source"], "manual")

    def test_save_report_defaults_unchanged(self):
        rid = db.save_report("Legacy Field", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS)
        report = db.get_report(rid)
        self.assertIsNone(report["farmer_id"])
        self.assertIsNone(report["plot_id"])
        self.assertEqual(report["source"], "manual")

    def test_list_reports_farmer_filter(self):
        f1 = db.create_farmer("Farmer One")
        f2 = db.create_farmer("Farmer Two")
        db.save_report("A", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS, farmer_id=f1)
        db.save_report("B", 45, 18, 90, 6.4, "Moderate", SAMPLE_RECS, farmer_id=f2)
        db.save_report("C", 50, 20, 120, 6.5, "Good", SAMPLE_RECS)
        self.assertEqual(len(db.list_reports(farmer_id=f1)), 1)
        self.assertEqual(db.list_reports(farmer_id=f1)[0]["plot"], "A")
        self.assertEqual(len(db.list_reports()), 3)

    def test_get_report(self):
        rid = db.save_report("Field", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS, crops=SAMPLE_CROPS)
        report = db.get_report(rid)
        self.assertEqual(report["id"], rid)
        self.assertEqual(report["recommendations"][0]["element"], "Nitrogen")
        self.assertEqual(report["crops"][0]["crop"], "rice")
        self.assertIsNone(db.get_report(9999))


class TestOutcomes(AgentDbTestCase):
    def test_upsert_creates_then_replaces(self):
        rid = db.save_report("Field", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS)
        first = db.upsert_outcome(
            rid, implementation_notes="Urea split", implemented_at="2026-05-01",
            expected_yield=3.0, yield_unit="t/ha",
        )
        self.assertEqual(first["report_id"], rid)
        self.assertEqual(first["expected_yield"], 3.0)
        self.assertIsNone(first["actual_yield"])

        second = db.upsert_outcome(
            rid, implementation_notes="Urea split", implemented_at="2026-05-01",
            expected_yield=3.0, actual_yield=2.7, yield_unit="t/ha",
            harvest_at="2026-09-15",
        )
        rows = db.list_reports()
        self.assertEqual(len(rows), 1)
        outcome = db.get_outcome(rid)
        self.assertEqual(outcome["id"], second["id"])
        self.assertEqual(outcome["actual_yield"], 2.7)
        self.assertEqual(outcome["harvest_at"], "2026-09-15")

    def test_get_outcome_missing(self):
        rid = db.save_report("Field", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS)
        self.assertIsNone(db.get_outcome(rid))


class TestFarmerDetail(AgentDbTestCase):
    def test_detail_none_for_unknown(self):
        self.assertIsNone(db.farmer_detail(9999))

    def test_detail_shape(self):
        fid = db.create_farmer("Abebe", region="Oromia")
        pid = db.create_plot(fid, "North Ridge")
        rid = db.save_report("North Ridge", 25, 10, 40, 5.5, "Poor", SAMPLE_RECS,
                             farmer_id=fid, plot_id=pid, crops=SAMPLE_CROPS)
        db.upsert_outcome(rid, implementation_notes="Lime applied", expected_yield=3.0)

        detail = db.farmer_detail(fid)
        self.assertEqual(detail["farmer"]["name"], "Abebe")
        self.assertEqual(len(detail["plots"]), 1)
        self.assertEqual(len(detail["reports"]), 1)
        report = detail["reports"][0]
        self.assertEqual(report["outcome"]["implementation_notes"], "Lime applied")
        self.assertEqual(report["recommendations"][0]["element"], "Nitrogen")


class TestMigration(AgentDbTestCase):
    def test_old_schema_gains_columns_in_place(self):
        """A database created before Phase 1 (no farmer/plot/source columns)
        must migrate without losing data."""
        db.cleanup(config.DB_PATH)
        conn = sqlite3.connect(config.DB_PATH)
        conn.execute(
            """CREATE TABLE reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plot TEXT NOT NULL,
                created_at TEXT NOT NULL,
                N REAL NOT NULL, P REAL NOT NULL, K REAL NOT NULL, ph REAL NOT NULL,
                crop TEXT, temperature REAL, humidity REAL, rainfall REAL,
                fertility_status TEXT NOT NULL,
                recommendations TEXT NOT NULL,
                crops TEXT,
                lang TEXT DEFAULT 'en'
            )"""
        )
        conn.execute(
            """INSERT INTO reports
               (plot, created_at, N, P, K, ph, fertility_status, recommendations, lang)
               VALUES ('Old Field', '2025-01-01T00:00:00+00:00', 25, 10, 40, 5.5,
                       'Poor', ?, 'en')""",
            (json.dumps(SAMPLE_RECS),),
        )
        conn.commit()
        conn.close()

        db.init_db()

        conn = sqlite3.connect(config.DB_PATH)
        columns = {r[1] for r in conn.execute("PRAGMA table_info('reports')")}
        conn.close()
        self.assertTrue({"farmer_id", "plot_id", "source"} <= columns)

        reports = db.list_reports()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["plot"], "Old Field")
        self.assertIsNone(reports[0]["farmer_id"])
        self.assertEqual(reports[0]["source"], "manual")

        # New writes on the migrated DB work with agent fields.
        fid = db.create_farmer("Post-migration")
        rid = db.save_report("Old Field", 45, 18, 90, 6.4, "Moderate", SAMPLE_RECS,
                             farmer_id=fid)
        self.assertEqual(db.get_report(rid)["farmer_id"], fid)


if __name__ == "__main__":
    unittest.main()
