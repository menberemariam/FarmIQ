import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

import config
import db

TOOLS = PROJECT_ROOT / "tools"


class TestTrainingTools(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._prev_db_path = config.DB_PATH
        config.DB_PATH = Path(self.tmpdir.name) / "tools.db"
        db.init_db()
        self.sample_id = db.save_training_sample(
            25, 10, 40, 5.5, report_id=7, consent=True, crop="maize",
            agroecological_zone="highland_nitisols", soil_type="nitisol",
            source="lab",
        )
        db.save_training_sample(
            30, 12, 45, 6.0, report_id=8, consent=True, crop="wheat",
            agroecological_zone="highland_vertisols", soil_type="vertisol",
            source="lab",
        )

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def _run(self, *args):
        result = subprocess.run(
            [sys.executable, *map(str, args)],
            cwd=PROJECT_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(f"{args[0]} failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout

    def _run_expect_failure(self, *args):
        return subprocess.run(
            [sys.executable, *map(str, args)],
            cwd=PROJECT_ROOT, capture_output=True, text=True)

    def test_import_then_export(self):
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        with lab_csv.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=[
                "report_id", "lab_N", "lab_P", "lab_K", "lab_ph",
                "input_units", "lab_status", "verified_by",
            ])
            writer.writeheader()
            writer.writerow({
                "report_id": 7, "lab_N": 28, "lab_P": 11, "lab_K": 42,
                "lab_ph": 5.8, "input_units": "kg_ha", "lab_status": "Good",
                "verified_by": "lab-tech",
            })

        self._run(TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
                  "--csv", lab_csv, "--apply")
        sample = db.get_training_sample(self.sample_id)
        self.assertEqual(sample["label_status"], "Good")
        self.assertEqual(sample["label_source"], "lab_measured")
        self.assertEqual(sample["lab_N"], 28)
        self.assertEqual(sample["N"], 25)

        out = Path(self.tmpdir.name) / "real.csv"
        meta = Path(self.tmpdir.name) / "real.meta.json"
        self._run(TOOLS / "export_training_data.py", "--db", config.DB_PATH,
                  "--out", out, "--meta", meta, "--force")
        with out.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Fertility_Status"], "Good")
        self.assertEqual(float(rows[0]["N"]), 28.0)
        self.assertEqual(rows[0]["source"], "real")
        metadata = json.loads(meta.read_text())
        self.assertEqual(metadata["rows"], 1)
        self.assertEqual(metadata["label_source_counts"], {"lab_measured": 1})
        self.assertEqual(
            metadata["dataset_sha256"],
            hashlib.sha256(out.read_bytes()).hexdigest())

    def test_dry_run_does_not_write_labels(self):
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        lab_csv.write_text(
            "report_id,lab_N,lab_P,lab_K,lab_ph,lab_status\n"
            "7,28,11,42,5.8,Good\n")
        self._run(TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
                  "--csv", lab_csv)
        sample = db.get_training_sample(self.sample_id)
        self.assertIsNone(sample["label_status"])

    def test_duplicate_content_is_rejected(self):
        # Same physical measurement re-entered under a different crop.
        db.save_training_sample(
            25, 10, 40, 5.5, report_id=9, consent=True, crop="sorghum",
            agroecological_zone="highland_nitisols", soil_type="nitisol",
            source="lab",
        )
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        lab_csv.write_text(
            "report_id,lab_N,lab_P,lab_K,lab_ph,lab_status\n"
            "7,28,11,42,5.8,Good\n"
            "9,28,11,42,5.8,Good\n")
        result = self._run_expect_failure(
            TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
            "--csv", lab_csv, "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate measurement", result.stdout)
        verified = db.list_training_samples(
            include_in_training=True, only_verified=True, limit=100)
        self.assertEqual(len(verified), 1)

    def test_duplicate_content_allowed_with_flag(self):
        db.save_training_sample(
            25, 10, 40, 5.5, report_id=9, consent=True, crop="sorghum",
            agroecological_zone="highland_nitisols", soil_type="nitisol",
            source="lab",
        )
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        lab_csv.write_text(
            "report_id,lab_N,lab_P,lab_K,lab_ph,lab_status\n"
            "7,28,11,42,5.8,Good\n"
            "9,28,11,42,5.8,Good\n")
        self._run(TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
                  "--csv", lab_csv, "--apply", "--allow-duplicates")
        verified = db.list_training_samples(
            include_in_training=True, only_verified=True, limit=100)
        self.assertEqual(len(verified), 2)

    def test_require_coverage_gate(self):
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        lab_csv.write_text(
            "report_id,lab_N,lab_P,lab_K,lab_ph,lab_status\n"
            "7,28,11,42,5.8,Good\n")
        # Incremental import is allowed by default even though the set is thin.
        self._run(TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
                  "--csv", lab_csv, "--apply")
        result = self._run_expect_failure(
            TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
            "--csv", lab_csv, "--apply", "--require-coverage")
        self.assertEqual(result.returncode, 1)
        self.assertIn("coverage below minimum", result.stdout)

    def test_zone_inferred_from_woreda(self):
        sample_id = db.save_training_sample(
            31, 13, 50, 6.2, report_id=10, consent=True,
            soil_type="cambisol_luvisol", source="lab")
        defaults = Path(self.tmpdir.name) / "woredas.json"
        defaults.write_text(json.dumps({
            "woredas": {"Adaa": {"agroecological_zone": "rift_valley"}}
        }))
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        lab_csv.write_text(
            "report_id,lab_N,lab_P,lab_K,lab_ph,lab_status,woreda\n"
            "10,33,14,55,6.4,Moderate,Adaa\n")
        self._run(TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
                  "--csv", lab_csv, "--apply",
                  "--infer-zone-from-woreda", "--woreda-defaults", defaults)
        sample = db.get_training_sample(sample_id)
        self.assertEqual(sample["agroecological_zone"], "rift_valley")

    def test_export_carries_site_id_and_woreda(self):
        db.update_training_sample_context(self.sample_id, woreda="Adaa")
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        lab_csv.write_text(
            "report_id,lab_N,lab_P,lab_K,lab_ph,lab_status\n"
            "7,28,11,42,5.8,Good\n")
        self._run(TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
                  "--csv", lab_csv, "--apply")
        out = Path(self.tmpdir.name) / "real.csv"
        self._run(TOOLS / "export_training_data.py", "--db", config.DB_PATH,
                  "--out", out, "--force")
        rows = pd.read_csv(out).to_dict("records")
        self.assertEqual(rows[0]["woreda"], "Adaa")
        self.assertEqual(rows[0]["site_id"], "Adaa")
        meta = json.loads(out.with_suffix(".meta.json").read_text())
        self.assertEqual(meta["site_counts"], {"Adaa": 1})

    def test_evaluator_refuses_synthetic_only(self):
        fixture = Path(self.tmpdir.name) / "eval.csv"
        pd.read_csv(config.SOIL_DATA_ETHIO_V6_CSV).head(300).to_csv(
            fixture, index=False)
        result = self._run_expect_failure(
            TOOLS / "evaluate_ethio_model.py", "--csv", fixture)
        self.assertEqual(result.returncode, 2)
        self.assertIn("No verified real rows", result.stdout)

    def test_evaluator_gate_fails_below_threshold(self):
        fixture = Path(self.tmpdir.name) / "eval.csv"
        pd.read_csv(config.SOIL_DATA_ETHIO_V6_CSV).head(300).to_csv(
            fixture, index=False)
        result = self._run_expect_failure(
            TOOLS / "evaluate_ethio_model.py", "--csv", fixture,
            "--allow-synthetic", "--min-macro-f1", "1.1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("GATE FAILED", result.stdout)

    def test_evaluator_uses_real_rows_only(self):
        # Mechanics fixture: real-provenance rows selected, synthetic ignored.
        frame = pd.read_csv(config.SOIL_DATA_ETHIO_V6_CSV).head(240)
        frame["source"] = "real"
        frame["site_id"] = frame["zone"]
        synthetic = pd.read_csv(config.SOIL_DATA_ETHIO_V6_CSV).head(120)
        synthetic["source"] = "synthetic"
        synthetic["site_id"] = "synthetic:" + synthetic["zone"].astype(str)
        fixture = Path(self.tmpdir.name) / "mixed.csv"
        pd.concat([frame, synthetic], ignore_index=True).to_csv(
            fixture, index=False)
        report = Path(self.tmpdir.name) / "report.json"
        self._run(TOOLS / "evaluate_ethio_model.py", "--csv", fixture,
                  "--min-macro-f1", "0.0", "--json-out", report)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["provenance"], "real")
        self.assertEqual(payload["rows_evaluated"], 240)

    def test_evaluator_runs_and_writes_json(self):
        fixture = Path(self.tmpdir.name) / "eval.csv"
        pd.read_csv(config.SOIL_DATA_ETHIO_V6_CSV).head(300).to_csv(
            fixture, index=False)
        report = Path(self.tmpdir.name) / "report.json"
        self._run(TOOLS / "evaluate_ethio_model.py", "--csv", fixture,
                  "--allow-synthetic", "--min-macro-f1", "0.0",
                  "--json-out", report)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["provenance"], "synthetic")
        self.assertIn("rule_baseline", payload)
        self.assertTrue(payload["gates"]["passed"])

    def test_combine_with_synthetic(self):
        lab_csv = Path(self.tmpdir.name) / "labs.csv"
        lab_csv.write_text(
            "report_id,lab_N,lab_P,lab_K,lab_ph,lab_status\n"
            "7,28,11,42,5.8,Good\n")
        self._run(TOOLS / "import_lab_results.py", "--db", config.DB_PATH,
                  "--csv", lab_csv, "--apply")

        base = Path(self.tmpdir.name) / "base.csv"
        base.write_text(
            "N,P,K,ph,zone,soil_type,Fertility_Status\n"
            "20,10,100,6.0,highland_nitisols,nitisol,Moderate\n")
        out = Path(self.tmpdir.name) / "real.csv"
        combined = Path(self.tmpdir.name) / "v7.csv"
        self._run(
            TOOLS / "export_training_data.py", "--db", config.DB_PATH,
            "--out", out, "--force", "--combine-with", base,
            "--combined-out", combined, "--force")
        with combined.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["source"] for row in rows},
                         {"real", "synthetic"})
        synthetic = [row for row in rows if row["source"] == "synthetic"]
        self.assertEqual(synthetic[0]["site_id"],
                         "synthetic:highland_nitisols")
        self.assertTrue(combined.with_suffix(".meta.json").exists())


if __name__ == "__main__":
    unittest.main()
