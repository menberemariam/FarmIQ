"""Phase 4 hardening tests: control-token gate (F3) + demo/simulator
single-writer rules (F4)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import db  # noqa: E402
import iot  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

TOKEN = "token-SOIL-001"
CONTROL = "s3cret-control-token"


class ControlHardeningTestCase(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_control.db"
        db.init_db()
        plot_id = db.create_plot(db.create_farmer("Abebe Getachew"),
                                 "North Ridge")
        db.register_device("SOIL-001", "Prototype 01", plot_id, TOKEN)
        # Park the demo device so Run Demo can bind without _ensure creating it.
        iot.stop_simulation()

    def tearDown(self):
        iot.stop_simulation()
        if iot.demo_run_active():
            iot.release_demo_slot()
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()


class TestControlTokenGate(ControlHardeningTestCase):
    """F3: IOT_CONTROL_TOKEN gates demo-control endpoints when set."""

    def test_default_is_open_without_configuration(self):
        env = {k: v for k, v in os.environ.items() if k != "IOT_CONTROL_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            resp = client.post("/api/iot/demo/run",
                               json={"scenario": "normal", "count": 3})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["simulation_stopped"])

    def test_simulation_rejects_missing_and_wrong_token(self):
        with mock.patch.dict(os.environ, {"IOT_CONTROL_TOKEN": CONTROL}):
            missing = client.post("/api/iot/simulation", json={"action": "stop"})
            wrong = client.post(
                "/api/iot/simulation", json={"action": "stop"},
                headers={"X-Control-Token": "nope"},
            )
            self.assertEqual(missing.status_code, 401)
            self.assertEqual(wrong.status_code, 401)
            self.assertEqual(missing.json()["detail"],
                             wrong.json()["detail"])

    def test_simulation_accepts_correct_token(self):
        with mock.patch.dict(os.environ, {"IOT_CONTROL_TOKEN": CONTROL}):
            resp = client.post(
                "/api/iot/simulation", json={"action": "stop"},
                headers={"X-Control-Token": CONTROL},
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_demo_run_gated_when_token_set(self):
        with mock.patch.dict(os.environ, {"IOT_CONTROL_TOKEN": CONTROL}):
            missing = client.post("/api/iot/demo/run", json={"count": 3})
            ok = client.post(
                "/api/iot/demo/run", json={"count": 3},
                headers={"X-Control-Token": CONTROL},
            )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_read_state_stays_open(self):
        with mock.patch.dict(os.environ, {"IOT_CONTROL_TOKEN": CONTROL}):
            resp = client.get("/api/iot/simulation")
        self.assertEqual(resp.status_code, 200)


class TestDemoSimulatorExclusivity(ControlHardeningTestCase):
    """F4: demo backfill and simulator never write for the same device."""

    def _start_sim(self, device_id="SOIL-001"):
        resp = client.post(
            "/api/iot/simulation",
            json={"action": "start", "device_id": device_id,
                  "scenario": "dry", "interval_seconds": 60},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp

    def test_demo_stops_simulator_bound_to_demo_device(self):
        self._start_sim("SOIL-001")
        self.assertTrue(iot.simulation_state()["running"])
        resp = client.post("/api/iot/demo/run",
                           json={"scenario": "normal", "count": 5})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["simulation_stopped"])
        self.assertFalse(iot.simulation_state()["running"])
        self.assertEqual(body["readings_created"], 5)

    def test_simulator_on_other_device_keeps_running(self):
        plot_id = db.create_plot(db.create_farmer("Kebede Alemu"), "East Field")
        db.register_device("SOIL-002", "Prototype 02", plot_id, "token-002")
        self._start_sim("SOIL-002")
        resp = client.post("/api/iot/demo/run",
                           json={"scenario": "normal", "count": 5})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["simulation_stopped"])
        self.assertTrue(iot.simulation_state()["running"])

    def test_concurrent_demo_runs_rejected_with_409(self):
        self.assertTrue(iot.acquire_demo_slot())
        try:
            resp = client.post("/api/iot/demo/run", json={"count": 3})
            self.assertEqual(resp.status_code, 409)
        finally:
            iot.release_demo_slot()
        # Slot released → the same call succeeds again.
        resp = client.post("/api/iot/demo/run", json={"count": 3})
        self.assertEqual(resp.status_code, 200)

    def test_start_rejected_while_demo_holds_slot(self):
        self.assertTrue(iot.acquire_demo_slot())
        try:
            resp = client.post(
                "/api/iot/simulation",
                json={"action": "start", "device_id": "SOIL-001"},
            )
            self.assertEqual(resp.status_code, 409)
        finally:
            iot.release_demo_slot()

    def test_stop_allowed_while_demo_holds_slot(self):
        # Stopping reduces writers — it must stay available during a demo run.
        self.assertTrue(iot.acquire_demo_slot())
        try:
            resp = client.post("/api/iot/simulation", json={"action": "stop"})
            self.assertEqual(resp.status_code, 200)
        finally:
            iot.release_demo_slot()


if __name__ == "__main__":
    unittest.main()
