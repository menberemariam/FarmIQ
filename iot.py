"""Simulated IoT pipeline: contract, quality validation and the simulator.

This module is deliberately framework-free (no FastAPI imports) so both
`api.py` and `tools/iot_simulator.py` share the same generator and quality
rules. The JSON contract produced here is the exact contract a future
ESP32/PCB device will send — today the values are *simulated*
(`source = iot_simulator`) and must be labeled as such in every UI.

Units: N/P/K in kg/ha (the model's units), moisture %, temperature °C,
pH unitless. Converting raw sensor output into these units is a firmware
concern, documented in the README.
"""

import asyncio
import secrets
import threading
from datetime import datetime, timedelta, timezone

import alerts
import db

# Physical sensor bounds enforced on the device contract (a violation is a
# rejected reading, not a flagged one). Keys are contract names.
BOUNDS = {
    "ph": (0.0, 14.0),
    "nitrogen": (0.0, 1000.0),
    "phosphorus": (0.0, 1000.0),
    "potassium": (0.0, 1000.0),
    "moisture": (0.0, 100.0),
    "temperature": (-40.0, 80.0),
}

UNITS = {
    "ph": "pH",
    "nitrogen": "kg/ha",
    "phosphorus": "kg/ha",
    "potassium": "kg/ha",
    "moisture": "%",
    "temperature": "°C",
}

MAX_FUTURE_SECONDS = 300   # timestamps more than 5 min ahead are invalid
MAX_AGE_SECONDS = 86400    # older than 24h → stored with STALE flag

# Flag a reading when it jumps further than this from the previous one.
JUMP_THRESHOLDS = {
    "ph": 1.0,
    "N": 40.0,
    "P": 40.0,
    "K": 40.0,
    "moisture": 15.0,
    "temperature": 8.0,
}

# Scenario baselines — chosen inside the model's training ranges
# (N 0-140, P 5-145, K 5-205, pH 3.5-9.94) so assessments stay honest.
SCENARIOS = {
    "normal": {"ph": 6.5, "N": 55.0, "P": 25.0, "K": 110.0,
               "moisture": 45.0, "temperature": 22.0},
    "nitrogen_deficient": {"ph": 6.0, "N": 18.0, "P": 22.0, "K": 105.0,
                           "moisture": 42.0, "temperature": 23.0},
    "acidic": {"ph": 4.8, "N": 45.0, "P": 12.0, "K": 95.0,
               "moisture": 40.0, "temperature": 24.0},
    "dry": {"ph": 6.2, "N": 50.0, "P": 24.0, "K": 108.0,
            "moisture": 12.0, "temperature": 27.0},
}

# Per-tick noise (uniform ±) and mean-reversion pull toward the baseline —
# a scenario switch drifts values visibly over ~10 ticks instead of
# teleporting, which is how a continuously measuring device behaves.
NOISE = {"ph": 0.03, "N": 1.5, "P": 1.5, "K": 1.5,
         "moisture": 1.5, "temperature": 0.4}
REVERSION = 0.25
# Internal (model) key → contract key, for bounds clamping while walking.
_CONTRACT_KEY = {"ph": "ph", "N": "nitrogen", "P": "phosphorus",
                 "K": "potassium", "moisture": "moisture",
                 "temperature": "temperature"}

BATTERY_DRAIN_PER_TICK = 0.02
BATTERY_FLOOR = 40.0
DEFAULT_INTERVAL = 5.0


class IngestError(Exception):
    """Base class for ingestion failures; `status` maps to an HTTP status."""
    status = 400

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class Unauthorized(IngestError):
    status = 401


class DeviceNotFound(IngestError):
    status = 404


class InvalidReading(IngestError):
    status = 422


def generate_token():
    """Device auth token (demo-grade, unique per device)."""
    return secrets.token_hex(16)


def generate_device_id():
    """Readable default device id, e.g. SOIL-3F2A1B."""
    return f"SOIL-{secrets.token_hex(3).upper()}"


def new_state(scenario="normal"):
    """Initial simulator state: values start exactly on the baseline."""
    baseline = SCENARIOS[scenario]
    return {**baseline, "battery": 87.0, "signal": -61.0}


def step_state(state, scenario="normal"):
    """One mean-reverting random-walk step; returns a new state dict."""
    baseline = SCENARIOS[scenario]
    next_state = dict(state)
    for key, noise in NOISE.items():
        reverted = state[key] + REVERSION * (baseline[key] - state[key])
        value = reverted + (secrets.randbelow(2001) / 1000 - 1) * noise
        lo, hi = BOUNDS[_CONTRACT_KEY[key]]
        next_state[key] = round(min(max(value, lo), hi), 2)
    next_state["battery"] = max(
        BATTERY_FLOOR, round(state["battery"] - BATTERY_DRAIN_PER_TICK, 2)
    )
    next_state["signal"] = float(-75 + secrets.randbelow(21))  # simulated dBm
    return next_state


def build_payload(state, device_id, location=None, timestamp=None):
    """Build the standardized device contract from a simulator state."""
    ts = timestamp or datetime.now(timezone.utc)
    payload = {
        "deviceId": device_id,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "measurements": {
            "ph": round(state["ph"], 2),
            "nitrogen": round(state["N"], 1),
            "phosphorus": round(state["P"], 1),
            "potassium": round(state["K"], 1),
            "moisture": round(state["moisture"], 1),
            "temperature": round(state["temperature"], 1),
        },
        "units": dict(UNITS),
        "heartbeat": {
            "battery": round(state["battery"], 1),
            "signal": round(state["signal"]),
        },
    }
    if location and location.get("latitude") is not None:
        payload["location"] = {
            "latitude": location["latitude"],
            "longitude": location.get("longitude"),
        }
    return payload


def parse_timestamp(value):
    """Accept ISO-8601 (with or without Z); naive timestamps are UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise InvalidReading(f"timestamp is not ISO-8601: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _quality_flags(previous, internal, reading_dt, now):
    """Stale + sudden-jump checks (physical bounds are checked upstream)."""
    flags = []
    if (now - reading_dt).total_seconds() > MAX_AGE_SECONDS:
        flags.append("STALE_TIMESTAMP")
    if (reading_dt - now).total_seconds() > MAX_FUTURE_SECONDS:
        raise InvalidReading(
            f"timestamp is more than {MAX_FUTURE_SECONDS}s in the future"
        )
    if previous is not None:
        for field, threshold in JUMP_THRESHOLDS.items():
            if abs(internal[field] - previous[field]) > threshold:
                flags.append("SUDDEN_VALUE_CHANGE")
                break
    return flags


def _store(device, payload):
    """Quality pipeline + persistence for one contract payload.

    Returns (reading_id, quality, flags). Raises InvalidReading for a
    future timestamp.
    """
    m = payload["measurements"]
    now = datetime.now(timezone.utc)
    reading_dt = parse_timestamp(payload["timestamp"])
    internal = {
        "ph": m["ph"], "N": m["nitrogen"], "P": m["phosphorus"],
        "K": m["potassium"], "moisture": m["moisture"],
        "temperature": m["temperature"],
    }
    previous = db.latest_reading(device["id"])
    flags = _quality_flags(previous, internal, reading_dt, now)
    quality = "warning" if flags else "valid"

    location = payload.get("location") or {}
    reading_id = db.insert_reading(
        device["id"], "iot_simulator",
        internal["N"], internal["P"], internal["K"], internal["ph"],
        internal["moisture"], internal["temperature"],
        quality, flags, reading_dt.isoformat(),
        latitude=location.get("latitude"), longitude=location.get("longitude"),
    )

    heartbeat = payload.get("heartbeat") or {}
    db.touch_device(
        device["id"], now.isoformat(),
        battery=heartbeat.get("battery"), signal=heartbeat.get("signal"),
    )
    db.prune_readings(device["id"])
    try:
        # Advisory states / alerts (Phase 3). Guarded: an alerting bug can
        # never fail an ingest — readings always come first.
        battery = heartbeat.get("battery")
        if battery is None:
            battery = device.get("battery")
        alerts.evaluate(device["id"], internal, flags, reading_id, battery=battery)
    except Exception:
        pass
    return reading_id, quality, flags


def ingest_reading(payload, token):
    """Validate + store one contract payload (the device-facing path).

    Returns (reading_id, quality, flags, duplicate). `duplicate` is True when
    the exact (device, timestamp) pair was already stored: the replay is
    acknowledged with the original id, no new row is written and no alerts are
    re-evaluated (idempotent ingest).

    Auth order is deliberate: the token is checked before the device lookup so
    unknown device IDs and wrong tokens are indistinguishable (401 for both) —
    the endpoint must not be usable to enumerate registered devices.

    Raises Unauthorized / DeviceNotFound / InvalidReading — the API maps
    `.status` to HTTP.
    """
    if not token:
        raise Unauthorized("missing or invalid X-Device-Token")
    device = db.get_device(payload["deviceId"])
    if device is None or not secrets.compare_digest(
        str(token), str(device["token"])
    ):
        raise Unauthorized("missing or invalid X-Device-Token")

    m = payload["measurements"]
    for field, (lo, hi) in BOUNDS.items():
        if not (lo <= m[field] <= hi):
            raise InvalidReading(
                f"{field}={m[field]} outside physical bounds [{lo}, {hi}]"
            )

    # Normalize the timestamp exactly like _store stores it, then honour
    # idempotent replays (same device + same timestamp = same reading).
    reading_dt = parse_timestamp(payload["timestamp"])
    stored = db.find_reading(device["id"], reading_dt.isoformat())
    if stored is not None:
        return stored["id"], stored["quality"], stored["flags"], True

    reading_id, quality, flags = _store(device, payload)
    return reading_id, quality, flags, False


def device_status(device, now=None):
    """Derive ONLINE/OFFLINE from last_seen (≤30s window), else never_seen."""
    now = now or datetime.now(timezone.utc)
    last_seen = device.get("last_seen_at")
    if not last_seen:
        return {"status": "never_seen", "seconds_since_last": None}
    seconds = (now - parse_timestamp(last_seen)).total_seconds()
    return {
        "status": "online" if seconds <= 30 else "offline",
        "seconds_since_last": round(seconds),
    }


# ---------------- In-process simulation loop ----------------

_SIM = {
    "running": False,
    "scenario": "normal",
    "interval": DEFAULT_INTERVAL,
    "device_row": None,
    "device_id": None,
    "location": None,
    "ticks": 0,
    "last_tick_at": None,
    "state": None,
    "task": None,
}


def simulation_state():
    """Public view of the simulation (no internal handles)."""
    return {
        "running": _SIM["running"],
        "scenario": _SIM["scenario"],
        "interval_seconds": _SIM["interval"],
        "device_id": _SIM["device_id"],
        "ticks": _SIM["ticks"],
        "last_tick_at": _SIM["last_tick_at"],
    }


def tick():
    """One generate → store cycle (used by the loop and by tests).

    Returns (reading_id, quality, flags), or None if the bound device
    disappeared (the loop stops itself in that case).
    """
    device_row = _SIM["device_row"]
    if device_row is None:
        return None
    device = db.get_device(_SIM["device_id"])
    if device is None:
        stop_simulation()
        return None

    _SIM["state"] = step_state(_SIM["state"], _SIM["scenario"])
    payload = build_payload(_SIM["state"], _SIM["device_id"], _SIM["location"])
    result = _store(device, payload)
    _SIM["ticks"] += 1
    _SIM["last_tick_at"] = datetime.now(timezone.utc).isoformat()
    return result


def backfill_readings(device, scenario="normal", count=20, reset=False):
    """Reproducible demo history: `count` readings at 5-minute steps spanning
    the last count*5 minutes (all within 24h → fresh, no STALE flag), walked
    through the chosen scenario so values evolve believably. Each reading
    runs the full quality + advisory pipeline via `_store`, exactly like
    live device traffic.

    Returns the number of readings created.
    """
    if scenario not in SCENARIOS:
        raise InvalidReading(f"unknown scenario '{scenario}'")
    if reset:
        db.clear_readings(device["id"])
    plot = db.get_plot(device["plot_id"])
    location = (
        {"latitude": plot["latitude"], "longitude": plot["longitude"]}
        if plot and plot.get("latitude") is not None
        else None
    )
    state = new_state(scenario)
    now = datetime.now(timezone.utc)
    created = 0
    for i in range(count):
        # Reading 0 sits exactly on the scenario baseline; later ones walk.
        if i:
            state = step_state(state, scenario)
        ts = now - timedelta(seconds=300 * (count - 1 - i))
        payload = build_payload(state, device["device_id"],
                                location=location, timestamp=ts)
        _store(device, payload)
        created += 1
    return created


async def _simulation_loop():
    try:
        while _SIM["running"]:
            tick()
            await asyncio.sleep(_SIM["interval"])
    except asyncio.CancelledError:
        pass


def start_simulation(device_row, device_id, location, scenario="normal",
                     interval=DEFAULT_INTERVAL):
    """Start (or reconfigure) the background simulator task.

    Calling again while running updates scenario/interval live — the loop
    reads the shared state every tick.
    """
    if scenario not in SCENARIOS:
        raise InvalidReading(f"unknown scenario '{scenario}'")
    _SIM.update(
        running=True, scenario=scenario, interval=interval,
        device_row=device_row, device_id=device_id, location=location,
        ticks=0, last_tick_at=None, state=new_state(scenario),
    )
    task = _SIM.get("task")
    # Recreate when absent, finished, or bound to a closed loop (test
    # clients may rebuild the event loop per request).
    if task is None or task.done() or task.get_loop().is_closed():
        _SIM["task"] = asyncio.get_running_loop().create_task(_simulation_loop())


def stop_simulation():
    """Stop the background simulator and unbind the device."""
    _SIM["running"] = False
    task = _SIM.get("task")
    if task is not None and not task.done() and not task.get_loop().is_closed():
        task.cancel()
    _SIM["task"] = None
    _SIM["device_row"] = None
    _SIM["device_id"] = None
    _SIM["state"] = None


# ---------------- single-writer guard: demo run vs simulator (F4) ----------
# Run Demo (backfill) and simulator control must never write for the same
# device at the same time, and two demo runs must not overlap. The demo slot
# is held for the whole backfill; simulator start refuses while it is taken,
# and a demo run stops a simulator bound to its own device before writing.

_demo_lock = threading.Lock()


def demo_run_active():
    """True while a Run Demo backfill is in progress."""
    return _demo_lock.locked()


def acquire_demo_slot():
    """Non-blocking: False when another demo run already holds the slot."""
    return _demo_lock.acquire(blocking=False)


def release_demo_slot():
    _demo_lock.release()
