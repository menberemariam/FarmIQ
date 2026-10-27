import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import db


class IotDbTestCase(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "iot_test.db"
        db.init_db()
        self.farmer_id = db.create_farmer("Abebe Getachew")
        self.plot_id = db.create_plot(self.farmer_id, "North Ridge", latitude=7.95, longitude=39.13)

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()

    def _register(self, device_id="SOIL-001"):
        return db.register_device(
            device_id, "Oromia Soil Prototype 01", self.plot_id,
            token=f"token-{device_id}", battery=87, signal=-61,
        )


class TestDeviceCrud(IotDbTestCase):
    def test_register_and_get(self):
        row_id = self._register()
        device = db.get_device("SOIL-001")
        self.assertEqual(device["id"], row_id)
        self.assertEqual(device["name"], "Oromia Soil Prototype 01")
        self.assertEqual(device["plot_id"], self.plot_id)
        self.assertEqual(device["firmware"], "0.1.0")
        self.assertEqual(device["token"], "token-SOIL-001")
        self.assertEqual(device["battery"], 87)
        self.assertEqual(device["signal"], -61)
        self.assertIsNone(device["last_seen_at"])
        self.assertEqual(device["sensors"], db.DEFAULT_SENSORS)
        self.assertIsNone(db.get_device("NOPE"))

    def test_duplicate_device_id_rejected(self):
        self._register()
        with self.assertRaises(Exception):
            self._register()

    def test_list_devices(self):
        self.assertEqual(db.list_devices(), [])
        self._register("SOIL-001")
        self._register("SOIL-002")
        devices = db.list_devices()
        self.assertEqual([d["device_id"] for d in devices], ["SOIL-001", "SOIL-002"])

    def test_touch_device_updates_heartbeat(self):
        self._register()
        db.touch_device(1, "2026-09-27T16:00:00+00:00", battery=86.9, signal=-63)
        device = db.get_device("SOIL-001")
        self.assertEqual(device["last_seen_at"], "2026-09-27T16:00:00+00:00")
        self.assertEqual(device["battery"], 86.9)
        self.assertEqual(device["signal"], -63)


class TestReadings(IotDbTestCase):
    def test_insert_list_order_and_parse(self):
        device_row = self._register()
        for i, ts in enumerate(("2026-09-27T16:00:00+00:00",
                                "2026-09-27T16:00:05+00:00")):
            db.insert_reading(
                device_row, "iot_simulator", 55, 25, 110, 6.5, 45, 22,
                "valid", [], ts, latitude=7.95, longitude=39.13,
            )
        readings = db.list_readings(device_row)
        self.assertEqual(len(readings), 2)
        # newest first
        self.assertEqual(readings[0]["reading_ts"], "2026-09-27T16:00:05+00:00")
        self.assertEqual(readings[0]["flags"], [])
        self.assertEqual(readings[0]["source"], "iot_simulator")
        self.assertEqual(readings[0]["moisture"], 45)

    def test_latest_reading(self):
        device_row = self._register()
        self.assertIsNone(db.latest_reading(device_row))
        db.insert_reading(device_row, "iot_simulator", 55, 25, 110, 6.5, 45, 22,
                          "valid", [], "2026-09-27T16:00:00+00:00")
        db.insert_reading(device_row, "iot_simulator", 50, 24, 108, 6.2, 40, 23,
                          "warning", ["SUDDEN_VALUE_CHANGE"],
                          "2026-09-27T16:00:05+00:00")
        latest = db.latest_reading(device_row)
        self.assertEqual(latest["quality"], "warning")
        self.assertEqual(latest["flags"], ["SUDDEN_VALUE_CHANGE"])

    def test_prune_keeps_newest(self):
        device_row = self._register()
        for i in range(30):
            db.insert_reading(device_row, "iot_simulator", 55, 25, 110, 6.5, 45, 22,
                              "valid", [], f"2026-09-27T16:{i:02d}:00+00:00")
        db.prune_readings(device_row, keep=10)
        readings = db.list_readings(device_row, limit=100)
        self.assertEqual(len(readings), 10)
        # the survivors are the newest timestamps
        self.assertEqual(readings[0]["reading_ts"], "2026-09-27T16:29:00+00:00")
        self.assertEqual(readings[-1]["reading_ts"], "2026-09-27T16:20:00+00:00")


if __name__ == "__main__":
    unittest.main()
