import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import db
import api
from fastapi.testclient import TestClient

client = TestClient(api.app)

REPORT = {
    "plot": "Training Field", "N": 25, "P": 10, "K": 40, "ph": 5.5,
    "fertility_status": "Poor",
    "recommendations": [{
        "element": "Nitrogen", "status": "deficient", "deficit": 10.0,
        "solution": "Apply urea.", "source": "rule-based",
    }],
    "crop": "maize",
    "agroecological_zone": "highland_nitisols",
    "soil_type": "nitisol",
    "consent_for_training": True,
}


class TestTrainingCapture(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._prev_db_path = config.DB_PATH
        config.DB_PATH = Path(self.tmpdir.name) / "capture.db"
        db.init_db()

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def test_capture_requires_consent_and_flag(self):
        with mock.patch.object(config, "training_capture_enabled",
                               return_value=False):
            resp = client.post("/api/reports", json=REPORT)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.training_sample_counts()["total"], 0)

        with mock.patch.object(config, "training_capture_enabled",
                               return_value=True):
            resp = client.post("/api/reports", json={**REPORT,
                                                     "consent_for_training":
                                                     False})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.training_sample_counts()["total"], 0)

    def test_capture_stores_unlabeled_context(self):
        with mock.patch.object(config, "training_capture_enabled",
                               return_value=True):
            resp = client.post("/api/reports", json=REPORT)
        self.assertEqual(resp.status_code, 200)
        samples = db.list_training_samples()
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample["consent"], 1)
        self.assertIsNone(sample["label_status"])
        self.assertEqual(sample["include_in_training"], 0)
        self.assertEqual(sample["agroecological_zone"], "highland_nitisols")
        self.assertEqual(sample["soil_type"], "nitisol")
        self.assertEqual(sample["report_id"], resp.json()["id"])

    def test_consent_omitted_writes_nothing(self):
        payload = {key: value for key, value in REPORT.items()
                   if key != "consent_for_training"}
        with mock.patch.object(config, "training_capture_enabled",
                               return_value=True):
            resp = client.post("/api/reports", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.training_sample_counts()["total"], 0)

    def test_health_reports_capture_flag(self):
        with mock.patch.object(config, "training_capture_enabled",
                               return_value=False):
            body = client.get("/api/health").json()
        self.assertIs(body["training_capture"], False)

        with mock.patch.object(config, "training_capture_enabled",
                               return_value=True):
            body = client.get("/api/health").json()
        self.assertIs(body["training_capture"], True)

    def test_unknown_soil_type_is_rejected(self):
        with mock.patch.object(config, "training_capture_enabled",
                               return_value=True):
            resp = client.post("/api/reports", json={**REPORT,
                                                     "soil_type": "moon_dust"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(db.training_sample_counts()["total"], 0)


if __name__ == "__main__":
    unittest.main()
