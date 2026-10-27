import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import db


class TestTrainingSamples(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._prev_db_path = config.DB_PATH
        config.DB_PATH = Path(self.tmpdir.name) / "training.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def _sample(self, **overrides):
        values = {
            "N": 25, "P": 10, "K": 40, "ph": 5.5,
            "consent": True, "crop": "maize",
            "agroecological_zone": "highland_nitisols",
            "soil_type": "nitisol",
        }
        values.update(overrides)
        return db.save_training_sample(**values)

    def test_consent_is_required(self):
        self.assertIsNone(self._sample(consent=False))
        self.assertEqual(db.training_sample_counts()["total"], 0)

    def test_identical_samples_deduplicate(self):
        first = self._sample()
        second = self._sample()
        self.assertEqual(first, second)
        self.assertEqual(db.training_sample_counts()["total"], 1)

    def test_hash_includes_ethiopian_context(self):
        first = self._sample()
        second = self._sample(agroecological_zone="rift_valley")
        third = self._sample(soil_type="vertisol")
        self.assertEqual(len({first, second, third}), 3)
        self.assertEqual(db.training_sample_counts()["total"], 3)

    def test_verification_adds_label_without_touching_raw_values(self):
        sample_id = self._sample()
        updated = db.verify_training_sample(
            sample_id, "Good", "lab_measured", label_confidence=0.9,
            verified_by="lab-1", dataset_version="real-v1",
            lab_N=30, lab_P=12, lab_K=45, lab_ph=6.0)
        self.assertEqual(updated["label_status"], "Good")
        self.assertEqual(updated["label_source"], "lab_measured")
        self.assertEqual(updated["lab_N"], 30)
        self.assertEqual(updated["N"], 25)
        self.assertEqual(updated["include_in_training"], 1)
        counts = db.training_sample_counts()
        self.assertEqual(counts["included"], 1)
        self.assertEqual(counts["by_label_status"], {"Good": 1})

    def test_find_by_report_id(self):
        sample_id = self._sample(report_id=42)
        found = db.find_training_sample(report_id=42)
        self.assertEqual(found["id"], sample_id)

    def test_context_update_refreshes_hash(self):
        sample_id = self._sample(soil_type=None)
        db.update_training_sample_context(sample_id, soil_type="nitisol")
        updated = db.get_training_sample(sample_id)
        self.assertEqual(updated["soil_type"], "nitisol")
        self.assertTrue(updated["sample_hash"])


if __name__ == "__main__":
    unittest.main()
