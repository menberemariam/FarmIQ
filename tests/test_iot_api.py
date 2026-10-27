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


def _now_iso(delta=timedelta(0)):
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def _measurements(**overrides):
    base = {"ph": 6.5, "nitrogen": 55, "phosphorus": 25, "potassium": 110,
            "moisture": 45, "temperature": 22}
    base.update(overrides)
    return base


def _payload(device_id, ts=None, measurements=None):
    return {
        "deviceId": device_id,
        "timestamp": ts or _now_iso(),
        "measurements": measurements or _measurements(),
        "units": dict(iot.UNITS),
        "location": {"latitude": 7.95, "longitude": 39.13},
        "heartbeat": {"battery": 86.5, "signal": -60},
    }


class IotApiTestCase(unittest.TestCase):
    """Per-test throwaway DB; config.DB_PATH restored for other modules."""

    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_iot_api.db"
        db.init_db()
        self.farmer_id = db.create_farmer("Abebe Getachew")
        self.plot_id = db.create_plot(
            self.farmer_id, "North Ridge", latitude=7.95, longitude=39.13
        )

    def tearDown(self):
        iot.stop_simulation()
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def _register(self, device_id="SOIL-001", **extra):
        body = {"name": "Oromia Soil Prototype 01", "plot_id": self.plot_id,
                "device_id": device_id}
        body.update(extra)
        resp = client.post("/api/iot/devices", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _ingest(self, payload, token):
        return client.post("/api/iot/readings", json=payload,
                           headers={"X-Device-Token": token})


class TestDeviceEndpoints(IotApiTestCase):
    def test_register_returns_token_and_hides_it_in_list(self):
        reg = self._register()
        self.assertTrue(reg["token"])
        self.assertEqual(reg["device_id"], "SOIL-001")

        devices = client.get("/api/iot/devices").json()["devices"]
        self.assertEqual(len(devices), 1)
        self.assertNotIn("token", devices[0])
        self.assertEqual(devices[0]["plot_name"], "North Ridge")
        self.assertEqual(devices[0]["farmer_id"], self.farmer_id)
        self.assertEqual(devices[0]["status"], "never_seen")
        self.assertIsNone(devices[0]["latest_reading"])

    def test_register_generates_id_when_omitted(self):
        reg = self._register(device_id=None)
        self.assertRegex(reg["device_id"], r"^SOIL-[0-9A-F]{6}$")

    def test_register_duplicate_rejected(self):
        self._register()
        resp = client.post("/api/iot/devices", json={
            "name": "Dup", "plot_id": self.plot_id, "device_id": "SOIL-001"})
        self.assertEqual(resp.status_code, 409)

    def test_register_unknown_plot_rejected(self):
        resp = client.post("/api/iot/devices", json={
            "name": "Orphan", "plot_id": 999, "device_id": "SOIL-X"})
        self.assertEqual(resp.status_code, 404)

    def test_register_bad_device_id_rejected(self):
        resp = client.post("/api/iot/devices", json={
            "name": "Bad", "plot_id": self.plot_id, "device_id": "bad id!"})
        self.assertEqual(resp.status_code, 422)

    def test_get_device_and_404(self):
        self._register()
        device = client.get("/api/iot/devices/SOIL-001").json()["device"]
        self.assertEqual(device["name"], "Oromia Soil Prototype 01")
        self.assertNotIn("token", device)
        self.assertEqual(
            client.get("/api/iot/devices/NOPE").status_code, 404)


class TestReadingIngest(IotApiTestCase):
    def test_valid_reading_saved(self):
        reg = self._register()
        resp = self._ingest(_payload("SOIL-001"), reg["token"])
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["saved"])
        self.assertEqual(body["quality"], "valid")
        self.assertEqual(body["flags"], [])

        # device heartbeat + status updated
        device = client.get("/api/iot/devices/SOIL-001").json()["device"]
        self.assertEqual(device["status"], "online")
        self.assertEqual(device["battery"], 86.5)
        self.assertEqual(device["latest_reading"]["moisture"], 45)

    def test_missing_token_unauthorized(self):
        self._register()
        resp = client.post("/api/iot/readings", json=_payload("SOIL-001"))
        self.assertEqual(resp.status_code, 401)

    def test_wrong_token_unauthorized(self):
        self._register()
        resp = self._ingest(_payload("SOIL-001"), "not-the-token")
        self.assertEqual(resp.status_code, 401)

    def test_unknown_device_unauthorized_no_enumeration(self):
        # Phase 4 hardening (F1): unknown IDs answer 401 — same as a wrong
        # token — so ingest can't be used to probe which devices exist.
        reg = self._register()
        resp = self._ingest(_payload("GHOST-9"), reg["token"])
        self.assertEqual(resp.status_code, 401)

    def test_out_of_bounds_rejected_by_contract(self):
        reg = self._register()
        resp = self._ingest(
            _payload("SOIL-001", measurements=_measurements(moisture=120)),
            reg["token"])
        self.assertEqual(resp.status_code, 422)

    def test_future_timestamp_rejected(self):
        reg = self._register()
        resp = self._ingest(
            _payload("SOIL-001", ts=_now_iso(timedelta(minutes=30))),
            reg["token"])
        self.assertEqual(resp.status_code, 422)
        self.assertIn("future", resp.json()["detail"])

    def test_stale_reading_stored_with_flag(self):
        reg = self._register()
        resp = self._ingest(
            _payload("SOIL-001", ts=_now_iso(timedelta(hours=-48))),
            reg["token"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["quality"], "warning")
        self.assertEqual(resp.json()["flags"], ["STALE_TIMESTAMP"])

    def test_sudden_jump_flagged_not_dropped(self):
        reg = self._register()
        self._ingest(_payload("SOIL-001"), reg["token"])
        resp = self._ingest(
            _payload("SOIL-001", measurements=_measurements(nitrogen=140)),
            reg["token"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["quality"], "warning")
        self.assertEqual(resp.json()["flags"], ["SUDDEN_VALUE_CHANGE"])

    def test_bad_timestamp_rejected(self):
        reg = self._register()
        resp = self._ingest(_payload("SOIL-001", ts="not-a-date"), reg["token"])
        self.assertEqual(resp.status_code, 422)


class TestListReadings(IotApiTestCase):
    def test_list_newest_first(self):
        reg = self._register()
        self._ingest(_payload("SOIL-001"), reg["token"])
        self._ingest(_payload("SOIL-001"), reg["token"])
        resp = client.get("/api/iot/readings", params={"device_id": "SOIL-001"})
        self.assertEqual(resp.status_code, 200)
        readings = resp.json()["readings"]
        self.assertEqual(len(readings), 2)
        self.assertGreater(readings[0]["id"], readings[1]["id"])

    def test_unknown_device_404(self):
        resp = client.get("/api/iot/readings", params={"device_id": "NOPE"})
        self.assertEqual(resp.status_code, 404)


class TestDeviceAssessment(IotApiTestCase):
    def test_assessment_reuses_live_pipeline(self):
        reg = self._register()
        ing = self._ingest(_payload("SOIL-001"), reg["token"])
        resp = client.get("/api/iot/devices/SOIL-001/assessment")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn(body["fertility_status"], ("Poor", "Moderate", "Good"))
        self.assertTrue(body["recommendations"])
        self.assertTrue(body["crops"])
        self.assertTrue(body["simulated"])
        self.assertEqual(body["source"], "iot_simulator")
        self.assertEqual(body["reading_id"], ing.json()["id"])
        self.assertEqual(body["model_version"], api.MODEL_VERSION)

    def test_assessment_amharic(self):
        reg = self._register()
        self._ingest(_payload("SOIL-001"), reg["token"])
        resp = client.get("/api/iot/devices/SOIL-001/assessment",
                          params={"lang": "am"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["lang"], "am")

    def test_assessment_unknown_device(self):
        self.assertEqual(
            client.get("/api/iot/devices/NOPE/assessment").status_code, 404)

    def test_assessment_without_readings(self):
        self._register()
        resp = client.get("/api/iot/devices/SOIL-001/assessment")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("no readings", resp.json()["detail"])

    def test_assessment_bad_lang(self):
        reg = self._register()
        self._ingest(_payload("SOIL-001"), reg["token"])
        resp = client.get("/api/iot/devices/SOIL-001/assessment",
                          params={"lang": "xx"})
        self.assertEqual(resp.status_code, 422)


class TestReadingReport(IotApiTestCase):
    def test_reading_saved_to_history_with_device_attribution(self):
        reg = self._register()
        ing = self._ingest(_payload("SOIL-001"), reg["token"])
        reading_id = ing.json()["id"]

        resp = client.post(f"/api/iot/readings/{reading_id}/report",
                           json={"crop": "rice", "lang": "en"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["saved"])
        self.assertEqual(body["farmer_id"], self.farmer_id)

        reports = client.get("/api/reports",
                             params={"farmer_id": self.farmer_id}).json()
        saved = reports["reports"][0]
        self.assertEqual(saved["source"], "iot_simulator")
        self.assertEqual(saved["plot_id"], self.plot_id)
        self.assertEqual(saved["N"], 55)
        self.assertEqual(saved["crop"], "rice")
        # the reading link is queryable from history
        reading = db.get_reading(reading_id)
        self.assertEqual(reading["device_public_id"], "SOIL-001")
        self.assertEqual(reading["plot_id"], self.plot_id)

    def test_unknown_reading_404(self):
        resp = client.post("/api/iot/readings/999/report", json={})
        self.assertEqual(resp.status_code, 404)

    def test_unknown_crop_400(self):
        reg = self._register()
        ing = self._ingest(_payload("SOIL-001"), reg["token"])
        resp = client.post(f"/api/iot/readings/{ing.json()['id']}/report",
                           json={"crop": "unicorn wheat"})
        self.assertEqual(resp.status_code, 400)


class TestSimulation(IotApiTestCase):
    def test_start_and_stop(self):
        self._register()
        resp = client.post("/api/iot/simulation", json={"action": "start"})
        self.assertEqual(resp.status_code, 200, resp.text)
        sim = resp.json()["simulation"]
        self.assertTrue(sim["running"])
        self.assertEqual(sim["scenario"], "normal")
        self.assertEqual(sim["device_id"], "SOIL-001")
        self.assertEqual(sim["ticks"], 0)

        state = client.get("/api/iot/simulation").json()["simulation"]
        self.assertTrue(state["running"])

        resp = client.post("/api/iot/simulation", json={"action": "stop"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["simulation"]["running"])

    def test_start_with_scenario_and_interval(self):
        self._register()
        resp = client.post("/api/iot/simulation", json={
            "action": "start", "scenario": "acidic", "interval_seconds": 2})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["simulation"]["scenario"], "acidic")
        self.assertEqual(resp.json()["simulation"]["interval_seconds"], 2)

    def test_start_with_unknown_scenario_422(self):
        self._register()
        resp = client.post("/api/iot/simulation",
                           json={"action": "start", "scenario": "volcano"})
        self.assertEqual(resp.status_code, 422)

    def test_start_without_devices_400(self):
        resp = client.post("/api/iot/simulation", json={"action": "start"})
        self.assertEqual(resp.status_code, 400)

    def test_start_with_unknown_device_404(self):
        resp = client.post("/api/iot/simulation",
                           json={"action": "start", "device_id": "GHOST"})
        self.assertEqual(resp.status_code, 404)

    def test_simulation_endpoints_require_no_token(self):
        # simulation control is a server-side admin action, not device traffic
        self._register()
        resp = client.post("/api/iot/simulation",
                           json={"action": "start", "device_id": "SOIL-001"})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
