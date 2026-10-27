import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import config  # noqa: E402

import db  # noqa: E402
import iot  # noqa: E402


class IotSimulatorTestCase(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "iot_sim.db"
        db.init_db()
        farmer_id = db.create_farmer("Abebe Getachew")
        self.plot_id = db.create_plot(
            farmer_id, "North Ridge", latitude=7.95, longitude=39.13
        )
        self.device_row = db.register_device(
            "SOIL-001", "Oromia Soil Prototype 01", self.plot_id,
            token="test-token",
        )

    def tearDown(self):
        iot.stop_simulation()
        iot._SIM.update(ticks=0, last_tick_at=None, scenario="normal",
                        location=None, interval=iot.DEFAULT_INTERVAL)
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def _bind_sim(self, scenario="normal", location=None):
        """Bind the in-process simulator without needing an event loop."""
        iot._SIM.update(
            running=True, scenario=scenario, interval=5.0,
            device_row=self.device_row, device_id="SOIL-001",
            location=location, ticks=0, last_tick_at=None,
            state=iot.new_state(scenario), task=None,
        )


class TestScenarios(IotApiContract := IotSimulatorTestCase):
    def test_baselines_are_inside_training_and_sensor_bounds(self):
        self.assertEqual(set(iot.SCENARIOS),
                         {"normal", "nitrogen_deficient", "acidic", "dry"})
        for name, baseline in iot.SCENARIOS.items():
            self.assertEqual(
                set(baseline), set(iot.NOISE), f"{name} fields mismatch")
            for key, value in baseline.items():
                lo, hi = iot.BOUNDS[iot._CONTRACT_KEY[key]]
                self.assertTrue(
                    lo <= value <= hi, f"{name}.{key}={value} out of bounds")
            # model training range sanity (N 0-140, K 5-205)
            self.assertLessEqual(baseline["N"], 140, name)
            self.assertLessEqual(baseline["K"], 205, name)

    def test_step_stays_in_bounds_over_many_ticks(self):
        state = iot.new_state("acidic")
        for _ in range(60):
            state = iot.step_state(state, "acidic")
            for key, value in state.items():
                if key in ("battery", "signal"):
                    continue
                lo, hi = iot.BOUNDS[iot._CONTRACT_KEY[key]]
                self.assertTrue(lo <= value <= hi, f"{key}={value}")
        self.assertGreaterEqual(state["battery"], iot.BATTERY_FLOOR)

    def test_battery_drains_to_floor(self):
        state = iot.new_state()
        state["battery"] = 40.01
        state = iot.step_state(state)
        self.assertEqual(state["battery"], iot.BATTERY_FLOOR)

    def test_scenario_switch_drifts_to_new_baseline(self):
        state = iot.new_state("normal")
        self.assertEqual(state["N"], 55.0)
        for _ in range(12):
            state = iot.step_state(state, "nitrogen_deficient")
        # converging toward 18, never teleporting (must have passed between)
        self.assertLess(state["N"], 30)
        self.assertGreater(state["N"], 5)
        self.assertAlmostEqual(state["ph"], 6.0, delta=0.6)

    def test_step_shape_and_ranges(self):
        before = iot.new_state("dry")
        after = iot.step_state(before, "dry")
        self.assertEqual(set(after), set(before))
        self.assertTrue(-75 <= after["signal"] <= -55)
        self.assertLessEqual(after["battery"], before["battery"])


class TestContractPayload(IotSimulatorTestCase):
    def test_payload_shape(self):
        payload = iot.build_payload(
            iot.new_state(), "SOIL-001",
            {"latitude": 7.95, "longitude": 39.13},
        )
        self.assertEqual(payload["deviceId"], "SOIL-001")
        self.assertTrue(payload["timestamp"].endswith("Z"))
        self.assertEqual(
            set(payload["measurements"]),
            {"ph", "nitrogen", "phosphorus", "potassium", "moisture",
             "temperature"})
        self.assertEqual(payload["units"], iot.UNITS)
        self.assertEqual(payload["units"]["nitrogen"], "kg/ha")
        self.assertEqual(payload["location"], {"latitude": 7.95,
                                               "longitude": 39.13})
        self.assertIn("battery", payload["heartbeat"])
        self.assertIn("signal", payload["heartbeat"])

    def test_payload_without_location(self):
        payload = iot.build_payload(iot.new_state(), "SOIL-001")
        self.assertNotIn("location", payload)

    def test_token_and_device_id_formats(self):
        self.assertNotEqual(iot.generate_token(), iot.generate_token())
        self.assertRegex(iot.generate_token(), r"^[0-9a-f]{32}$")
        self.assertRegex(iot.generate_device_id(), r"^SOIL-[0-9A-F]{6}$")


class TestTick(IotSimulatorTestCase):
    def test_tick_stores_reading_and_counts(self):
        self._bind_sim()
        reading_id, quality, flags = iot.tick()
        self.assertEqual(quality, "valid")
        self.assertEqual(flags, [])
        self.assertEqual(iot._SIM["ticks"], 1)
        self.assertIsNotNone(iot._SIM["last_tick_at"])

        reading = db.latest_reading(self.device_row)
        self.assertEqual(reading["id"], reading_id)
        self.assertEqual(reading["source"], "iot_simulator")
        # walks around the normal baseline (55), never teleporting
        self.assertAlmostEqual(reading["N"], 55, delta=5)

        iot.tick()
        self.assertEqual(iot._SIM["ticks"], 2)
        self.assertEqual(len(db.list_readings(self.device_row)), 2)

    def test_tick_drifts_within_scenario(self):
        self._bind_sim("dry")
        iot.tick()
        readings = db.list_readings(self.device_row, limit=10)
        for _ in range(5):
            iot.tick()
        readings = db.list_readings(self.device_row, limit=10)
        last = readings[0]
        # moisture walks around the dry baseline (12), far below normal (45)
        self.assertLess(last["moisture"], 30)
        self.assertTrue(all(r["quality"] == "valid" for r in readings))
        # ticks are strictly ordered
        self.assertEqual(len(readings), 6)
        self.assertGreater(readings[0]["id"], readings[-1]["id"])

    def test_tick_uses_bound_location(self):
        self._bind_sim(location={"latitude": 7.95, "longitude": 39.13})
        iot.tick()
        reading = db.latest_reading(self.device_row)
        self.assertEqual(reading["latitude"], 7.95)
        self.assertEqual(reading["longitude"], 39.13)

    def test_tick_stops_when_device_gone(self):
        self._bind_sim()
        iot._SIM["device_id"] = "GHOST-9"
        result = iot.tick()
        self.assertIsNone(result)
        self.assertFalse(iot._SIM["running"])

    def test_simulation_state_view_hides_internals(self):
        self._bind_sim()
        state = iot.simulation_state()
        self.assertEqual(
            set(state),
            {"running", "scenario", "interval_seconds", "device_id", "ticks",
             "last_tick_at"})
        self.assertTrue(state["running"])
        self.assertNotIn("token", str(state))


class TestDeviceStatus(IotSimulatorTestCase):
    def test_status_transitions(self):
        now = datetime.now(timezone.utc)
        never = iot.device_status({"last_seen_at": None}, now)
        self.assertEqual(never["status"], "never_seen")

        online = iot.device_status(
            {"last_seen_at": now.isoformat()}, now)
        self.assertEqual(online["status"], "online")
        self.assertEqual(online["seconds_since_last"], 0)

        stale = iot.device_status(
            {"last_seen_at": (now - timedelta(minutes=5)).isoformat()}, now)
        self.assertEqual(stale["status"], "offline")
        self.assertEqual(stale["seconds_since_last"], 300)


class TestCliSimulator(unittest.TestCase):
    """tools/iot_simulator.py with the HTTP layer stubbed out."""

    @staticmethod
    def _run(args, send_result=(200, {"saved": True})):
        import iot_simulator

        calls = []

        def fake_send(url, payload, token):
            calls.append((url, payload, token))
            return send_result

        with mock.patch.object(iot_simulator, "send", fake_send):
            code = iot_simulator.main(args)
        return code, calls

    def test_sends_contract_payloads(self):
        code, calls = self._run(
            ["--device", "SOIL-001", "--token", "t0k",
             "--count", "3", "--interval", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)
        for url, payload, token in calls:
            self.assertEqual(url, "http://localhost:8000")
            self.assertEqual(token, "t0k")
            self.assertEqual(payload["deviceId"], "SOIL-001")
            self.assertTrue(payload["timestamp"].endswith("Z"))
            self.assertEqual(
                set(payload["measurements"]),
                {"ph", "nitrogen", "phosphorus", "potassium", "moisture",
                 "temperature"})
            self.assertEqual(payload["units"], iot.UNITS)
        # the local walk advances between sends (same generator as server)
        ph_values = {p["measurements"]["ph"] for _, p, _ in calls}
        self.assertGreater(len(ph_values), 1)

    def test_scenario_drifts_toward_baseline(self):
        code, calls = self._run(
            ["--device", "SOIL-001", "--token", "t0k", "--count", "15",
             "--interval", "0", "--scenario", "nitrogen_deficient"])
        self.assertEqual(code, 0)
        last_n = calls[-1][1]["measurements"]["nitrogen"]
        self.assertLess(last_n, 30)  # converged from 55 toward 18

    def test_aborts_on_auth_error(self):
        code, calls = self._run(
            ["--device", "SOIL-001", "--token", "wrong", "--count", "5",
             "--interval", "0"],
            send_result=(401, {"detail": "invalid X-Device-Token"}))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)  # stops on first rejection

    def test_aborts_on_contract_rejection(self):
        code, calls = self._run(
            ["--device", "SOIL-001", "--token", "t0k", "--count", "5",
             "--interval", "0"],
            send_result=(422, {"detail": "future"}))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
