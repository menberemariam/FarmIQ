import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import alerts
import config
import db
import iot


def internal(ph=6.5, moisture=45.0, N=55.0, P=25.0, K=110.0, temperature=22.0):
    return {
        "ph": ph, "moisture": moisture, "N": N, "P": P, "K": K,
        "temperature": temperature,
    }


def types(fired):
    return [item["type"] for item in fired]


class TestRuleBands(unittest.TestCase):
    """Pure threshold evaluation — no DB needed."""

    def test_moisture_bands_are_exclusive(self):
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(moisture=10.0), [])), ["MOISTURE_CRITICAL"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(moisture=15.0), [])), ["MOISTURE_LOW"])
        # boundary: exactly 12% is low, not critical
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(moisture=12.0), [])), ["MOISTURE_LOW"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(moisture=25.0), [])), [])

    def test_ph_bands_are_exclusive(self):
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(ph=4.4), [])), ["TOO_ACIDIC_SEVERE"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(ph=4.8), [])), ["TOO_ACIDIC"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(ph=8.3), [])), ["TOO_ALKALINE"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(ph=8.7), [])), ["TOO_ALKALINE_SEVERE"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(ph=6.5), [])), [])

    def test_nitrogen_bands_are_exclusive(self):
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(N=18.0), [])), ["N_CRITICAL"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(N=35.0), [])), ["N_LOW"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(N=50.0), [])), [])

    def test_temperature_stress(self):
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(temperature=8.0), [])), ["TEMP_STRESS"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(temperature=40.0), [])), ["TEMP_STRESS"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(temperature=22.0), [])), [])

    def test_battery_rule_only_when_provided(self):
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(), [], battery=None)), [])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(), [], battery=15.0)), ["BATTERY_LOW"])
        self.assertEqual(types(alerts.evaluate_conditions(
            internal(), [], battery=80.0)), [])

    def test_data_quality_rules_from_flags(self):
        fired = alerts.evaluate_conditions(internal(), ["STALE_TIMESTAMP"])
        self.assertEqual(types(fired), ["DATA_STALE"])
        self.assertEqual(fired[0]["severity"], "warning")
        fired = alerts.evaluate_conditions(internal(), ["SUDDEN_VALUE_CHANGE"])
        self.assertEqual(types(fired), ["DATA_JUMP"])
        self.assertEqual(fired[0]["severity"], "info")

    def test_multiple_metrics_fire_together(self):
        fired = alerts.evaluate_conditions(
            internal(ph=4.8, moisture=15.0, N=18.0), ["SUDDEN_VALUE_CHANGE"]
        )
        self.assertEqual(
            set(types(fired)),
            {"TOO_ACIDIC", "MOISTURE_LOW", "N_CRITICAL", "DATA_JUMP"},
        )

    def test_severity_mapping_and_message_payload(self):
        fired = alerts.evaluate_conditions(internal(moisture=10.0), [])
        item = fired[0]
        self.assertEqual(item["severity"], "critical")
        self.assertEqual(item["value"], 10.0)
        self.assertEqual(item["threshold"], alerts.MOISTURE_CRITICAL)
        self.assertIn("critically low", item["message"])
        self.assertIn("10", item["message"])


class AlertDbTestCase(unittest.TestCase):
    def setUp(self):
        self._prev_db_path = config.DB_PATH
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = Path(self.tmpdir.name) / "alerts_test.db"
        db.init_db()
        farmer_id = db.create_farmer("Abebe Getachew")
        plot_id = db.create_plot(farmer_id, "North Ridge")
        self.device_row = db.register_device(
            "SOIL-001", "Oromia Soil Prototype 01", plot_id,
            token="token-SOIL-001", battery=87, signal=-61,
        )

    def tearDown(self):
        db.cleanup(config.DB_PATH)
        config.DB_PATH = self._prev_db_path
        self.tmpdir.cleanup()


class TestAlertLifecycle(AlertDbTestCase):
    def test_evaluate_opens_alert_with_deep_link_context(self):
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], reading_id=None)
        rows = db.list_alerts(self.device_row, status="active")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["alert_type"], "MOISTURE_CRITICAL")
        self.assertEqual(row["severity"], "critical")
        self.assertEqual(row["device_public_id"], "SOIL-001")
        self.assertEqual(row["device_name"], "Oromia Soil Prototype 01")
        self.assertEqual(row["farmer_id"], 1)
        self.assertEqual(row["resolved_reason"], None)
        self.assertEqual(db.count_active_alerts(self.device_row), 1)

    def test_dedup_one_active_per_device_and_type(self):
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
        alerts.evaluate(self.device_row, internal(moisture=9.0), [], None)
        alerts.evaluate(self.device_row, internal(moisture=8.0), [], None)
        active = db.list_alerts(self.device_row, status="active")
        self.assertEqual(len(active), 1)
        self.assertEqual(db.count_active_alerts(), 1)

    def test_auto_resolve_when_condition_clears(self):
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
        alerts.evaluate(self.device_row, internal(moisture=45.0), [], None)
        self.assertEqual(db.count_active_alerts(self.device_row), 0)
        resolved = db.list_alerts(self.device_row, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["resolved_reason"], "auto")

    def test_band_escalation_swaps_alert_types(self):
        # critical → condition eases into the warning band
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
        alerts.evaluate(self.device_row, internal(moisture=15.0), [], None)
        active = db.list_alerts(self.device_row, status="active")
        self.assertEqual([a["alert_type"] for a in active], ["MOISTURE_LOW"])
        all_rows = db.list_alerts(self.device_row, limit=10)
        self.assertEqual(len(all_rows), 2)
        critical = [a for a in all_rows if a["alert_type"] == "MOISTURE_CRITICAL"]
        self.assertEqual(critical[0]["resolved_reason"], "auto")

    def test_ack_resolves_once(self):
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
        alert = db.list_alerts(self.device_row, status="active")[0]
        acked = db.ack_alert(alert["id"])
        self.assertEqual(acked["status"], "resolved")
        self.assertEqual(acked["resolved_reason"], "acked")
        self.assertIsNone(db.ack_alert(alert["id"]))  # already resolved

    def test_ack_unknown_id_returns_none(self):
        self.assertIsNone(db.ack_alert(9999))

    def test_stored_value_threshold_and_reading_link(self):
        rid = db.insert_reading(
            self.device_row, "iot_simulator", 18, 25, 110, 6.5, 45, 22,
            "valid", [], "2026-09-28T10:00:00+00:00",
        )
        alerts.evaluate(self.device_row, internal(N=18.0), [], reading_id=rid)
        alert = db.list_alerts(self.device_row, status="active")[0]
        self.assertEqual(alert["alert_type"], "N_CRITICAL")
        self.assertEqual(alert["value"], 18.0)
        self.assertEqual(alert["threshold"], alerts.N_CRITICAL)
        self.assertEqual(alert["reading_id"], rid)


class TestEmailIsolationAndDelivery(AlertDbTestCase):
    """Email is env-gated: disabled by default, worker never in the way."""

    def setUp(self):
        super().setUp()
        self._prev_env = {
            key: os.environ.get(key)
            for key in ("ALERT_EMAIL_TO", "SMTP_HOST", "SMTP_PORT",
                        "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
        }
        self._prev_worker = alerts._WORKER_ALLOWED
        alerts._WORKER_ALLOWED = False

    def tearDown(self):
        alerts._WORKER_ALLOWED = self._prev_worker
        for key, value in self._prev_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def _enable_email(self):
        os.environ["ALERT_EMAIL_TO"] = "agronomist@example.org"
        os.environ["SMTP_HOST"] = "smtp.example.org"
        os.environ["SMTP_PORT"] = "2525"
        os.environ["SMTP_FROM"] = "alerts@example.org"

    def test_disabled_by_default_writes_no_outbox_rows(self):
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
        self.assertIsNone(alerts.email_config())
        self.assertIsNone(db.next_queued_email())
        self.assertEqual(db.count_active_alerts(self.device_row), 1)  # in-app still works

    def test_enabled_queues_email_with_subject_and_deep_link(self):
        self._enable_email()
        self.assertIsNotNone(alerts.email_config())
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
        row = db.next_queued_email()
        self.assertIsNotNone(row)
        self.assertEqual(row["to_addr"], "agronomist@example.org")
        self.assertIn("MOISTURE_CRITICAL", row["subject"])
        self.assertIn("Oromia Soil Prototype 01", row["subject"])
        self.assertIn("critically low", row["body"])
        self.assertIn("/agent/1", row["body"])  # farmer deep link
        self.assertIn("simulated", row["body"])  # honesty
        self.assertEqual(row["status"], "queued")

    def test_deliver_pending_marks_sent_via_injected_sender(self):
        self._enable_email()
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
        seen = []

        def fake_send(row, cfg):
            seen.append((row["id"], cfg["host"]))

        self.assertEqual(alerts.deliver_pending(sender=fake_send), "sent")
        self.assertEqual(len(seen), 1)
        self.assertIsNone(db.next_queued_email())
        # nothing queued anymore
        self.assertEqual(alerts.deliver_pending(sender=fake_send), "none")

    def test_delivery_failures_retry_then_fail(self):
        self._enable_email()
        alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)

        def broken(row, cfg):
            raise OSError("connection refused")

        self.assertEqual(alerts.deliver_pending(sender=broken), "retry")
        self.assertEqual(alerts.deliver_pending(sender=broken), "retry")
        self.assertEqual(alerts.deliver_pending(sender=broken), "failed")
        self.assertEqual(alerts.deliver_pending(sender=broken), "none")
        # outbox row is terminal-failed, not queued
        self.assertIsNone(db.next_queued_email())

    def test_worker_thread_delivers_queued_email(self):
        """ensure_worker() lazily starts a daemon that drains the outbox."""
        self._enable_email()
        alerts._WORKER_ALLOWED = True
        prev_poll = alerts.EMAIL_POLL_SECONDS
        alerts.EMAIL_POLL_SECONDS = 0.05
        try:
            with mock.patch.object(alerts, "_smtp_send") as smtp_send:
                alerts.evaluate(self.device_row, internal(moisture=10.0), [], None)
                self.assertIsNotNone(alerts._worker_thread)
                self.assertTrue(alerts._worker_thread.daemon)
                deadline = time.time() + 5
                while time.time() < deadline and db.next_queued_email() is not None:
                    time.sleep(0.05)
                self.assertIsNone(db.next_queued_email())
                smtp_send.assert_called_once()
                time.sleep(0.1)  # let the worker observe the empty outbox
        finally:
            alerts.EMAIL_POLL_SECONDS = prev_poll


class TestIngestGuard(AlertDbTestCase):
    """An alerting failure must never fail an ingest (the hard guarantee)."""

    def _payload(self):
        return {
            "deviceId": "SOIL-001",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "measurements": {
                "nitrogen": 55, "phosphorus": 25, "potassium": 110,
                "ph": 6.5, "moisture": 45, "temperature": 22,
            },
            "location": {"latitude": 7.95, "longitude": 39.13},
            "heartbeat": {"battery": 90, "signal": -60},
        }

    def test_ingest_succeeds_even_if_evaluate_raises(self):
        with mock.patch.object(
            alerts, "evaluate", side_effect=RuntimeError("rule engine broke")
        ):
            reading_id, quality, flags, _duplicate = iot.ingest_reading(
                self._payload(), "token-SOIL-001"
            )
        self.assertIsNotNone(reading_id)
        self.assertEqual(quality, "valid")
        self.assertEqual(db.list_readings(self.device_row, limit=5)[0]["id"],
                         reading_id)

    def test_ingest_opens_alerts_end_to_end(self):
        payload = self._payload()
        payload["measurements"]["moisture"] = 9.0  # critical dryness
        reading_id, quality, flags, _duplicate = iot.ingest_reading(
            payload, "token-SOIL-001"
        )
        active = db.list_alerts(self.device_row, status="active")
        self.assertEqual([a["alert_type"] for a in active], ["MOISTURE_CRITICAL"])
        self.assertEqual(active[0]["reading_id"], reading_id)
        # device heartbeat updated + battery rule evaluated from payload
        device = db.get_device("SOIL-001")
        self.assertEqual(device["battery"], 90)


if __name__ == "__main__":
    unittest.main()
