"""Phase 4 hardening tests: ingest auth order (F1) + idempotency (F2)."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import db  # noqa: E402
import iot  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

TOKEN = "token-SOIL-001"


def _now_iso(delta=timedelta(0)):
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def _payload(device_id="SOIL-001", ts=None):
    return {
        "deviceId": device_id,
        "timestamp": ts or _now_iso(),
        "measurements": {
            "ph": 6.5, "nitrogen": 55, "phosphorus": 25, "potassium": 110,
            "moisture": 45, "temperature": 22,
        },
        "units": dict(iot.UNITS),
        "location": {"latitude": 7.95, "longitude": 39.13},
        "heartbeat": {"battery": 86.5, "signal": -60},
    }


class HardeningTestCase(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_hardening.db"
        db.init_db()
        self.plot_id = db.create_plot(db.create_farmer("Abebe Getachew"),
                                      "North Ridge")
        db.register_device("SOIL-001", "Prototype 01", self.plot_id, TOKEN)
        self.device_row = db.get_device("SOIL-001")["id"]

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()


class TestAuthOrder(HardeningTestCase):
    """F1: no device-ID enumeration on the ingest endpoint."""

    def test_unknown_device_returns_401_not_404(self):
        resp = client.post(
            "/api/iot/readings",
            json=_payload(device_id="GHOST-42"),
            headers={"X-Device-Token": "any-token"},
        )
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_unknown_and_wrong_token_are_indistinguishable(self):
        unknown = client.post(
            "/api/iot/readings",
            json=_payload(device_id="GHOST-42"),
            headers={"X-Device-Token": "whatever"},
        )
        wrong = client.post(
            "/api/iot/readings",
            json=_payload(device_id="SOIL-001"),
            headers={"X-Device-Token": "wrong"},
        )
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.json()["detail"], wrong.json()["detail"])

    def test_missing_token_still_401(self):
        resp = client.post("/api/iot/readings", json=_payload())
        self.assertEqual(resp.status_code, 401)

    def test_valid_token_still_accepted(self):
        resp = client.post(
            "/api/iot/readings", json=_payload(),
            headers={"X-Device-Token": TOKEN},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["duplicate"])

    def test_direct_call_auth_order(self):
        with self.assertRaises(iot.Unauthorized):
            iot.ingest_reading(_payload(device_id="GHOST-42"), "token")


class TestIdempotency(HardeningTestCase):
    """F2: replaying the same (device, timestamp) never duplicates data."""

    def test_replay_returns_original_id_without_new_row(self):
        payload = _payload(ts=_now_iso())
        first = client.post("/api/iot/readings", json=payload,
                            headers={"X-Device-Token": TOKEN})
        second = client.post("/api/iot/readings", json=payload,
                             headers={"X-Device-Token": TOKEN})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        body1, body2 = first.json(), second.json()
        self.assertFalse(body1["duplicate"])
        self.assertTrue(body2["duplicate"])
        self.assertEqual(body1["id"], body2["id"])
        self.assertEqual(len(db.list_readings(self.device_row, limit=50)), 1)

    def test_direct_ingest_tuple_shape(self):
        payload = _payload(ts=_now_iso())
        rid, quality, flags, dup = iot.ingest_reading(payload, TOKEN)
        self.assertFalse(dup)
        rid2, quality2, flags2, dup2 = iot.ingest_reading(payload, TOKEN)
        self.assertTrue(dup2)
        self.assertEqual(rid, rid2)
        self.assertEqual(quality, quality2)
        self.assertEqual(flags, flags2)

    def test_different_timestamp_stores_new_row(self):
        ts1, ts2 = _now_iso(), _now_iso(timedelta(minutes=5))
        first = client.post("/api/iot/readings", json=_payload(ts=ts1),
                            headers={"X-Device-Token": TOKEN})
        second = client.post("/api/iot/readings", json=_payload(ts=ts2),
                             headers={"X-Device-Token": TOKEN})
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertFalse(second.json()["duplicate"])
        self.assertEqual(len(db.list_readings(self.device_row, limit=50)), 2)

    def test_replay_does_not_fire_duplicate_alerts(self):
        payload = _payload(ts=_now_iso())
        payload["measurements"]["moisture"] = 9.0  # critical dryness
        client.post("/api/iot/readings", json=payload,
                    headers={"X-Device-Token": TOKEN})
        active_after_first = db.list_alerts(self.device_row, status="active")
        self.assertTrue(active_after_first)
        client.post("/api/iot/readings", json=payload,
                    headers={"X-Device-Token": TOKEN})
        active_after_replay = db.list_alerts(self.device_row, status="active")
        self.assertEqual(
            [a["id"] for a in active_after_first],
            [a["id"] for a in active_after_replay],
        )

    def test_replay_normalizes_z_suffix_timestamps(self):
        utc_now = datetime.now(timezone.utc)
        original = utc_now.isoformat().replace("+00:00", "Z")
        payload = _payload(ts=original)
        first = client.post("/api/iot/readings", json=payload,
                            headers={"X-Device-Token": TOKEN})
        # Same instant expressed as +00:00 instead of Z must still dedupe.
        payload["timestamp"] = utc_now.isoformat()
        second = client.post("/api/iot/readings", json=payload,
                             headers={"X-Device-Token": TOKEN})
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(first.json()["id"], second.json()["id"])


if __name__ == "__main__":
    unittest.main()
