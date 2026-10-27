"""Soil advisory states + the IoT → Farmer alert engine (Phase 3).

Every stored reading is checked against documented agronomic thresholds
and data-quality flags.  At most one *active* alert exists per
(device, alert_type); alerts auto-resolve when the condition clears and
can be acknowledged by an agent.  Notification email is optional and
env-gated (see `email_config`) — with no SMTP settings the engine is
in-app only, exactly as designed for offline demos.
"""

import os
import smtplib
import threading
import time
from email.message import EmailMessage

import db

# ---------------- advisory thresholds (agronomic, tunable in one place) --------

MOISTURE_CRITICAL = 12.0    # % volumetric — irrigation emergency
MOISTURE_LOW = 20.0         # % volumetric — schedule irrigation
PH_ACIDIC_SEVERE = 4.5      # lime-urgent acidity
PH_ACIDIC = 5.0             # acidity advisory
PH_ALKALINE = 8.0           # alkalinity advisory
PH_ALKALINE_SEVERE = 8.5    # severe alkalinity
N_CRITICAL = 20.0           # kg/ha available N — immediate deficiency
N_LOW = 40.0                # kg/ha available N — below ideal band (config.IDEAL_RANGES)
TEMP_MIN = 10.0             # °C — crop stress lower bound
TEMP_MAX = 35.0             # °C — crop stress upper bound
BATTERY_LOW = 20.0          # % — device will go offline soon

# Every type the engine knows about — the auto-resolve pass sweeps this
# list so a cleared condition never leaves a stale active alert behind.
ALL_TYPES = (
    "MOISTURE_CRITICAL", "MOISTURE_LOW",
    "TOO_ACIDIC_SEVERE", "TOO_ACIDIC", "TOO_ALKALINE_SEVERE", "TOO_ALKALINE",
    "N_CRITICAL", "N_LOW",
    "TEMP_STRESS", "BATTERY_LOW",
    "DATA_STALE", "DATA_JUMP",
)

_MESSAGES = {
    "MOISTURE_CRITICAL": "Soil moisture {value}% is critically low (below {threshold}%)",
    "MOISTURE_LOW": "Soil moisture {value}% is low (below {threshold}%)",
    "TOO_ACIDIC_SEVERE": "Soil pH {value} is severely acidic (below {threshold})",
    "TOO_ACIDIC": "Soil pH {value} is too acidic (below {threshold})",
    "TOO_ALKALINE_SEVERE": "Soil pH {value} is severely alkaline (above {threshold})",
    "TOO_ALKALINE": "Soil pH {value} is too alkaline (above {threshold})",
    "N_CRITICAL": "Available nitrogen {value} kg/ha is critically low (below {threshold})",
    "N_LOW": "Available nitrogen {value} kg/ha is low (below {threshold})",
    "TEMP_STRESS": "Temperature {value}°C is outside the safe crop range 10–35°C",
    "BATTERY_LOW": "Device battery at {value}% (below {threshold}%) — it may go offline",
    "DATA_STALE": "Reading timestamp is older than 24h — data may be stale",
    "DATA_JUMP": "Sudden value change detected between consecutive readings",
}


def evaluate_conditions(internal, flags, battery=None):
    """Pure rule evaluation: what advisory states does this reading fire?

    Returns a list of {type, severity, value, threshold, message} dicts.
    Bands are mutually exclusive per metric (critical supersedes warning),
    so a device shows one honest state per condition, never stacked alerts.
    `battery` is optional telemetry — skipped when the payload has none.
    """
    fired = []
    ph = internal["ph"]
    moisture = internal["moisture"]
    nitrogen = internal["N"]
    temperature = internal["temperature"]

    def add(alert_type, severity, value, threshold):
        fired.append({
            "type": alert_type,
            "severity": severity,
            "value": round(float(value), 2) if value is not None else None,
            "threshold": threshold,
            "message": _MESSAGES[alert_type].format(
                value=value if value is not None else "?",
                threshold=threshold if threshold is not None else "?",
            ),
        })

    if moisture < MOISTURE_CRITICAL:
        add("MOISTURE_CRITICAL", "critical", moisture, MOISTURE_CRITICAL)
    elif moisture < MOISTURE_LOW:
        add("MOISTURE_LOW", "warning", moisture, MOISTURE_LOW)

    if ph < PH_ACIDIC_SEVERE:
        add("TOO_ACIDIC_SEVERE", "critical", ph, PH_ACIDIC_SEVERE)
    elif ph < PH_ACIDIC:
        add("TOO_ACIDIC", "warning", ph, PH_ACIDIC)
    elif ph > PH_ALKALINE_SEVERE:
        add("TOO_ALKALINE_SEVERE", "critical", ph, PH_ALKALINE_SEVERE)
    elif ph > PH_ALKALINE:
        add("TOO_ALKALINE", "warning", ph, PH_ALKALINE)

    if nitrogen < N_CRITICAL:
        add("N_CRITICAL", "critical", nitrogen, N_CRITICAL)
    elif nitrogen < N_LOW:
        add("N_LOW", "warning", nitrogen, N_LOW)

    if temperature < TEMP_MIN or temperature > TEMP_MAX:
        add("TEMP_STRESS", "warning", temperature, None)

    if battery is not None and battery < BATTERY_LOW:
        add("BATTERY_LOW", "warning", battery, BATTERY_LOW)

    if "STALE_TIMESTAMP" in flags:
        add("DATA_STALE", "warning", None, None)
    if "SUDDEN_VALUE_CHANGE" in flags:
        add("DATA_JUMP", "info", None, None)

    return fired


def evaluate(device_row_id, internal, flags, reading_id, battery=None):
    """Open/close alerts for one stored reading.

    Fire rules → open any that are not already active (dedup) → auto-resolve
    every known type whose condition cleared.  Exceptions intentionally
    propagate: `iot._store` wraps this call in try/except so an alerting
    bug can never fail an ingest.
    """
    fired = evaluate_conditions(internal, flags, battery)
    fired_types = {item["type"] for item in fired}
    for item in fired:
        if db.get_active_alert(device_row_id, item["type"]) is None:
            _open_alert(device_row_id, reading_id, item)
    for alert_type in ALL_TYPES:
        if alert_type not in fired_types:
            db.resolve_active_alert(device_row_id, alert_type, "auto")


def _open_alert(device_row_id, reading_id, item):
    alert_id = db.create_alert(
        device_row_id, item["type"], item["severity"],
        item["value"], item["threshold"], reading_id, item["message"],
    )
    _maybe_enqueue_email(db.get_alert(alert_id))
    return alert_id


# ---------------- optional notification email (env-gated, default off) ----------

MAX_EMAIL_ATTEMPTS = 3
EMAIL_POLL_SECONDS = 3.0

# Test knob: unit tests exercise enqueue/delivery directly and never want a
# background thread polling their fake SMTP config.
_WORKER_ALLOWED = True
_worker_thread = None
_worker_lock = threading.Lock()


def email_config():
    """SMTP settings from env, or None when email delivery is disabled.

    Disabled (in-app only) unless BOTH ALERT_EMAIL_TO and SMTP_HOST are set —
    so an unconfigured demo never opens sockets, writes outbox rows, or
    depends on the internet.
    """
    to_addr = os.getenv("ALERT_EMAIL_TO")
    host = os.getenv("SMTP_HOST")
    if not to_addr or not host:
        return None
    return {
        "to_addr": to_addr,
        "host": host,
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD"),
        "from_addr": os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "alerts@soil-analysis.local",
    }


def _maybe_enqueue_email(alert):
    """Queue a notification email for a newly opened alert (never blocks)."""
    cfg = email_config()
    if cfg is None or alert is None:
        return None
    subject = f"[Soil Analysis] {alert['severity'].upper()}: {alert['device_name']} — {alert['alert_type']}"
    lines = [
        alert["message"],
        "",
        f"Device: {alert['device_name']} ({alert['device_public_id']})",
        f"Severity: {alert['severity']}",
        f"Opened: {alert['created_at']}",
    ]
    if alert.get("farmer_id") is not None:
        lines += ["", f"Open the farmer profile in the app: /agent/{alert['farmer_id']}"]
    lines += ["", "This is a simulated-device advisory from the Soil Analysis platform."]
    email_id = db.enqueue_alert_email(alert["id"], cfg["to_addr"], subject, "\n".join(lines))
    ensure_worker()
    return email_id


def _smtp_send(row, cfg):
    """Deliver one outbox row over SMTP (timeout-bounded, never called in tests)."""
    msg = EmailMessage()
    msg["From"] = cfg["from_addr"]
    msg["To"] = row["to_addr"]
    msg["Subject"] = row["subject"]
    msg.set_content(row["body"])
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as smtp:
        if cfg["port"] == 587:
            smtp.starttls()
        if cfg.get("user"):
            smtp.login(cfg["user"], cfg["password"] or "")
        smtp.send_message(msg)


def deliver_pending(sender=None):
    """Send the oldest queued email once.

    Returns "none" | "sent" | "retry" | "failed" (failed after MAX attempts).
    `sender(row, cfg)` is injectable for tests; defaults to `_smtp_send`.
    """
    row = db.next_queued_email()
    if row is None:
        return "none"
    cfg = email_config()
    if cfg is None:
        return "none"
    send = sender or _smtp_send
    attempts = row["attempts"] + 1
    try:
        send(row, cfg)
    except Exception as exc:  # noqa: BLE001 — any transport error is survivable
        status = "failed" if attempts >= MAX_EMAIL_ATTEMPTS else "queued"
        db.update_email_status(row["id"], status, attempts, error=str(exc)[:500])
        return "failed" if status == "failed" else "retry"
    db.update_email_status(row["id"], "sent", attempts)
    return "sent"


def ensure_worker():
    """Lazily start the daemon outbox worker (only when email is enabled)."""
    global _worker_thread
    if email_config() is None or not _WORKER_ALLOWED:
        return
    with _worker_lock:
        if _worker_thread is not None:
            return
        _worker_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="alert-email-worker"
        )
        _worker_thread.start()


def _worker_loop():
    while True:
        time.sleep(EMAIL_POLL_SECONDS)
        try:
            deliver_pending()
        except Exception:
            pass  # the worker must never die
