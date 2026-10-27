import csv
import io
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


class AlertApiTestCase(unittest.TestCase):
    """Per-test throwaway DB; config.DB_PATH restored for other modules."""

    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self._tmpdir.name) / "test_alert_api.db"
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

    def _register(self, device_id="SOIL-001"):
        resp = client.post("/api/iot/devices", json={
            "name": "Oromia Soil Prototype 01", "plot_id": self.plot_id,
            "device_id": device_id,
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _ingest(self, payload, token):
        return client.post("/api/iot/readings", json=payload,
                           headers={"X-Device-Token": token})


class TestAlertEndpoints(AlertApiTestCase):
    def test_empty_alerts(self):
        self._register()
        data = client.get("/api/iot/alerts").json()
        self.assertEqual(data["alerts"], [])
        self.assertEqual(data["active_count"], 0)

    def test_ingest_surfaces_alert_with_deep_link_context(self):
        reg = self._register()
        resp = self._ingest(
            _payload("SOIL-001", measurements=_measurements(moisture=9.0)),
            reg["token"],
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = client.get("/api/iot/alerts").json()
        self.assertEqual(data["active_count"], 1)
        alert = data["alerts"][0]
        self.assertEqual(alert["alert_type"], "MOISTURE_CRITICAL")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(alert["status"], "active")
        self.assertEqual(alert["device_public_id"], "SOIL-001")
        self.assertEqual(alert["device_name"], "Oromia Soil Prototype 01")
        self.assertEqual(alert["farmer_id"], self.farmer_id)
        self.assertEqual(alert["plot_id"], self.plot_id)
        self.assertEqual(alert["reading_id"], resp.json()["id"])
        self.assertIsNotNone(alert["value"])
        self.assertEqual(alert["threshold"], 12.0)

    def test_filter_by_device_and_404(self):
        reg = self._register()
        self._ingest(_payload("SOIL-001",
                              measurements=_measurements(moisture=9.0)),
                     reg["token"])
        data = client.get("/api/iot/alerts", params={"device_id": "SOIL-001"}).json()
        self.assertEqual(len(data["alerts"]), 1)
        self.assertEqual(data["active_count"], 1)
        resp = client.get("/api/iot/alerts", params={"device_id": "NOPE"})
        self.assertEqual(resp.status_code, 404)

    def test_ack_flow_and_status_filters(self):
        reg = self._register()
        self._ingest(_payload("SOIL-001",
                              measurements=_measurements(moisture=9.0)),
                     reg["token"])
        active = client.get("/api/iot/alerts").json()["alerts"]
        self.assertEqual(len(active), 1)
        alert_id = active[0]["id"]

        resp = client.post(f"/api/iot/alerts/{alert_id}/ack")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["acknowledged"])
        self.assertEqual(body["alert"]["resolved_reason"], "acked")
        self.assertEqual(body["active_count"], 0)

        # active view is empty; resolved view shows the acked alert
        self.assertEqual(client.get("/api/iot/alerts").json()["alerts"], [])
        resolved = client.get(
            "/api/iot/alerts", params={"status": "resolved"}
        ).json()["alerts"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["resolved_reason"], "acked")
        both = client.get("/api/iot/alerts", params={"status": "all"}).json()
        self.assertEqual(len(both["alerts"]), 1)

        # acking twice → 404
        resp = client.post(f"/api/iot/alerts/{alert_id}/ack")
        self.assertEqual(resp.status_code, 404)

    def test_ack_unknown_alert_404(self):
        resp = client.post("/api/iot/alerts/99999/ack")
        self.assertEqual(resp.status_code, 404)


class TestDemoRun(AlertApiTestCase):
    def test_run_creates_device_history_and_alerts(self):
        resp = client.post("/api/iot/demo/run", json={"scenario": "dry"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["device_id"], "SOIL-001")
        self.assertEqual(body["readings_created"], 20)
        self.assertIsInstance(body["active_alerts"], int)

        # demo chain exists and is bound to a farmer (deep links work)
        device = body["device"]
        self.assertEqual(device["plot_name"], "North Ridge")
        self.assertIsNotNone(device["farmer_id"])

        readings = client.get(
            "/api/iot/readings", params={"device_id": "SOIL-001", "limit": 100}
        ).json()["readings"]
        self.assertEqual(len(readings), 20)

        # all timestamps fresh: not future, not stale; no jump noise
        now = datetime.now(timezone.utc)
        for r in readings:
            ts = datetime.fromisoformat(
                r["reading_ts"].replace("Z", "+00:00"))
            self.assertLessEqual(ts, now + timedelta(seconds=300))
            self.assertGreater(ts, now - timedelta(hours=24))
            self.assertNotIn("STALE_TIMESTAMP", r["flags"])
            self.assertNotIn("SUDDEN_VALUE_CHANGE", r["flags"])

        # dry scenario history must contain a moisture advisory
        history = client.get(
            "/api/iot/alerts", params={"status": "all", "limit": 200}
        ).json()["alerts"]
        moisture_types = {a["alert_type"] for a in history
                          if a["alert_type"].startswith("MOISTURE")}
        self.assertTrue(
            moisture_types & {"MOISTURE_LOW", "MOISTURE_CRITICAL"},
            f"expected a moisture alert, got {moisture_types or 'none'}",
        )

    def test_run_resets_by_default_and_appends_when_reset_false(self):
        first = client.post("/api/iot/demo/run", json={"count": 10}).json()
        self.assertEqual(first["readings_created"], 10)
        second = client.post("/api/iot/demo/run", json={"count": 10}).json()
        self.assertEqual(second["readings_created"], 10)  # reset default
        readings = client.get(
            "/api/iot/readings", params={"device_id": "SOIL-001", "limit": 100}
        ).json()["readings"]
        self.assertEqual(len(readings), 10)

        third = client.post(
            "/api/iot/demo/run", json={"count": 5, "reset": False}
        ).json()
        self.assertEqual(third["readings_created"], 5)
        readings = client.get(
            "/api/iot/readings", params={"device_id": "SOIL-001", "limit": 100}
        ).json()["readings"]
        self.assertEqual(len(readings), 15)

    def test_run_validation(self):
        self.assertEqual(
            client.post("/api/iot/demo/run", json={"count": 0}).status_code, 422)
        self.assertEqual(
            client.post("/api/iot/demo/run", json={"count": 101}).status_code, 422)
        self.assertEqual(
            client.post("/api/iot/demo/run",
                        json={"scenario": "volcanic"}).status_code, 422)

    def test_run_on_seeded_db_reuses_existing_device(self):
        # setUp already created the demo farmer (device absent) — demo-run
        # must bind SOIL-001 to it instead of creating a duplicate farmer.
        body = client.post("/api/iot/demo/run", json={"count": 5}).json()
        self.assertEqual(body["readings_created"], 5)
        farmers = [f for f in db.list_farmer_summaries()
                   if f["name"] == "Abebe Getachew"]
        self.assertEqual(len(farmers), 1)
        self.assertEqual(body["device"]["farmer_id"], farmers[0]["id"])


class TestCsvExport(AlertApiTestCase):
    def test_export_contains_all_readings_chronologically(self):
        reg = self._register()
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        for i in range(3):
            resp = self._ingest(
                _payload("SOIL-001",
                         ts=(base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
                         measurements=_measurements(moisture=40 + i)),
                reg["token"],
            )
            self.assertEqual(resp.status_code, 200, resp.text)

        resp = client.get("/api/iot/readings/export",
                          params={"device_id": "SOIL-001"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])
        self.assertIn('filename="readings_SOIL-001.csv"',
                      resp.headers["content-disposition"])

        rows = list(csv.reader(io.StringIO(resp.text)))
        self.assertEqual(rows[0][:6],
                         ["id", "reading_ts", "received_at", "source",
                          "quality", "flags"])
        self.assertEqual(len(rows), 4)  # header + 3 readings
        # chronological: oldest first
        self.assertEqual(rows[1][1], rows[1][1])  # smoke: parseable
        self.assertLess(rows[1][1], rows[2][1])
        self.assertLess(rows[2][1], rows[3][1])
        # units columns present and values match what we ingested
        self.assertEqual(float(rows[1][10]), 40.0)  # moisture_pct
        self.assertEqual(rows[1][3], "iot_simulator")

    def test_export_unknown_device_404(self):
        resp = client.get("/api/iot/readings/export",
                          params={"device_id": "GHOST"})
        self.assertEqual(resp.status_code, 404)

    def test_export_header_only_when_no_readings(self):
        self._register()
        resp = client.get("/api/iot/readings/export",
                          params={"device_id": "SOIL-001"})
        self.assertEqual(resp.status_code, 200)
        rows = list(csv.reader(io.StringIO(resp.text)))
        self.assertEqual(len(rows), 1)

    def test_export_validation(self):
        self.assertEqual(
            client.get("/api/iot/readings/export",
                       params={"device_id": "SOIL-001", "limit": 0}
                       ).status_code, 422)
        self.assertEqual(
            client.get("/api/iot/readings/export").status_code, 422)


if __name__ == "__main__":
    unittest.main()
