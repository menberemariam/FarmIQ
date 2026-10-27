# ESP32 Firmware Guide — Sending Real Readings to the Platform

> **Status: conceptual documentation.** No hardware has been built yet. This
> document shows exactly how a future ESP32-based soil probe plugs into the
> platform **without any backend changes** — the HTTP contract below is the
> same one the simulator and CLI tool already use (`iot.py` →
> `POST /api/iot/readings`), and it is fully implemented and tested today.

## 1. Architecture

```
   ┌──────────────────────────────┐
   │  ESP32 + sensors (future)    │
   │  ─ reads probes, converts to │
   │    calibrated units          │
   └──────────────┬───────────────┘
                  │  HTTPS POST, same JSON contract
                  ▼
   POST /api/iot/readings        (X-Device-Token auth)
   ─ quality pipeline: bounds → 422, unknown device → 404,
     future timestamp → 422, stale/jump → warning flags
   ─ advisory engine: moisture/pH/N/temperature alerts
   ─ SQLite (iot_readings) → dashboard /assessment /CSV export
```

Three producers share one contract today: the in-process simulator, the CLI
(`tools/iot_simulator.py`), and later the firmware below. Nothing on the
server distinguishes them — readings keep `source = iot_simulator` until real
firmware sends with a registered device (then `iot_device`).

## 2. The contract the firmware must send

`POST /api/iot/readings` with header `X-Device-Token: <token>` (from
`POST /api/iot/devices` registration, one-time display):

```json
{
  "deviceId": "SOIL-001",
  "timestamp": "2026-09-28T10:15:00Z",
  "measurements": {
    "ph": 6.5,
    "nitrogen": 55,
    "phosphorus": 25,
    "potassium": 110,
    "moisture": 45,
    "temperature": 22
  },
  "units": { "nitrogen": "kg/ha", "phosphorus": "kg/ha", "potassium": "kg/ha",
             "moisture": "%", "temperature": "°C", "ph": "pH" },
  "location": { "latitude": 7.95, "longitude": 39.13 },
  "heartbeat": { "battery": 86.5, "signal": -60 }
}
```

Field rules enforced by the server (see `iot.py:BOUNDS`):

| Field | Range | Unit | Notes |
|---|---|---|---|
| ph | 0 – 14 | pH | reject outside → 422 |
| nitrogen / phosphorus / potassium | 0 – 1000 | **kg/ha** | model units, not ppm |
| moisture | 0 – 100 | % volumetric | |
| temperature | −40 – 80 | °C | |
| timestamp | ≤ 5 min in the future | ISO-8601 UTC (`Z`) | older than 24 h → `STALE_TIMESTAMP` flag (stored, not dropped) |
| battery / signal | 0–100 / −120–0 | % / dBm | optional heartbeat, simulated today |

## 3. Pseudocode (Arduino / ESP-IDF style)

```c
// --- one measurement cycle (e.g. every 5 minutes, deep sleep between) ---
void send_reading() {
  readings r = read_and_calibrate();      // see section 4

  StaticJsonDocument<512> doc;
  doc["deviceId"] = DEVICE_ID;            // e.g. "SOIL-001"
  doc["timestamp"] = iso8601_utc(now());  // MUST be UTC, Z suffix
  JsonObject m = doc.createNestedObject("measurements");
  m["ph"] = r.ph;                         // raw ADC → pH via 2-point calib
  m["nitrogen"]   = r.n_kg_ha;            // see honest note, section 4
  m["phosphorus"] = r.p_kg_ha;
  m["potassium"]  = r.k_kg_ha;
  m["moisture"]   = r.moisture_pct;
  m["temperature"] = r.temp_c;
  doc["heartbeat"]["battery"] = battery_pct();
  doc["heartbeat"]["signal"]  = wifi_rssi();

  HTTPClient http;
  http.begin(API_URL "/api/iot/readings");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);   // never hardcode in git
  int code = http.POST(serialize(doc));

  if (code == 401) rotate_or_reprovision_token();   // token revoked/typo
  if (code == 422) log_rejected_payload();          // fix calibration/clock
  // 404 → device not registered: register once via POST /api/iot/devices
  // warning quality (200 + quality=warning) → still success: flags stored
  http.end();
}
```

Operational rules:

- **Clock**: set time via SNTP before first send — the server rejects
  timestamps > 5 min in the future with 422.
- **Retry**: on network failure store to NVS/SD and resend oldest-first; the
  pipeline is idempotent-safe only per-reading (there is no server-side dedup
  key yet — do not blindly re-POST the same payload on ambiguous timeouts).
- **Cadence**: ≥ 60 s apart is polite; the dashboard's 5 s polling does not
  require faster devices.

## 4. Sensor → unit conversion (the firmware's job)

| Measurement | Suggested sensor | Conversion note |
|---|---|---|
| moisture % | Capacitive soil probe (e.g. capacitive v1.2) | dry/wet ADC calibration → 0–100 % |
| temperature °C | SHT31/BME280 (air) or buried DS18B20 | direct, ±0.3 °C |
| pH | Analog pH electrode + op-amp | 2-point calibration (pH 4.0 / 7.0) |
| N/P/K kg/ha | **Honest note**: true NPK needs lab extraction or an optical/NDIR proxy | a cheap EC sensor does **not** separate N, P, K — ship EC + lab correlation, or send dataset-typical baselines and let the agronomist correct them. Do **not** claim lab-grade NPK from a sub-$50 probe. |

This matches the platform's honesty policy: everything not physically measured
must be labeled (the UI already badges `source = iot_simulator` as
"Simulated data"; real firmware will badge `iot_device`).

## 5. From HTTP to MQTT later (no data-model change)

The quality pipeline, advisory engine and dashboard consume the **payload**,
not the transport. A future firmware can switch to MQTT (e.g. Mosquitto →
bridge/forwarder → same `POST /api/iot/readings`) without touching the data
model — only the ingestion front-end changes. Per the project's roadmap rules,
no MQTT cluster/Kafka/WebSocket infra is built now: the HTTP contract is the
stable seam.

## 6. Registration flow (once per device)

1. `POST /api/iot/devices` `{device_id, name, plot_id, sensors?, firmware?}`
   → `{token}` (shown once).
2. Store the token in NVS/flash (never in git).
3. Send readings with `X-Device-Token`.
4. `GET /api/iot/devices/{id}` shows status/last-seen; alerts appear in
   `/api/iot/alerts` and the dashboard bell automatically.
