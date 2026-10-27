# PCB & Sensor Architecture — Soil Probe Concept

> **Status: conceptual design, not manufactured.** This is the Priority-5
> hardware concept from the project roadmap: a schematic-level block diagram,
> sensor selection, power budget and communication plan for the field probe
> that will one day replace the simulator. The electrical design is
> intentionally kept at concept level — no PCB has been fabbed, and this
> document must not be presented as tested hardware.

## 1. Block diagram

```
                    ┌────────────────────────────────────────────┐
                    │                SOIL PROBE v0               │
                    │                                            │
  ┌──────────┐      │  ┌──────────────────────────────────────┐  │
  │ Capacitive│─────┼─▶│                                      │  │
  │ moisture  │ A/D │  │                                      │  │
  ├──────────┤      │  │            ESP32-C3 / S3             │  │
  │ pH        │─────┼─▶│  - Wi-Fi (HTTP → platform today)     │  │
  │ electrode │ amp │  │  - MQTT ready (same payload later)   │  │
  ├──────────┤      │  │  - deep sleep between readings       │  │
  │ Air/soil  │─────┼─▶│                                      │  │
  │ temp+RH   │ I²C │  └───────────────┬──────────────────────┘  │
  ├──────────┤      │                  │ SPI/I²C                  │
  │ NPK proxy │─────┼─▶  (optional) SD log / RS-485 to lab node  │
  └──────────┘      │                                            │
                    │  ┌───────────┐   ┌──────────────────────┐  │
                    │  │ LiPo 18650│──▶│ TP4056 charger       │  │
                    │  │ 2000–3000 │   │ + solar panel (opt.) │  │
                    │  └───────────┘   └──────────────────────┘  │
                    │        │                    │              │
                    │        └──────┬─────────────┘              │
                    │               ▼ 3.3 V LDO                  │
                    │  ┌──────────────────────────────────────┐  │
                    │  │ IP67 enclosure + probe stakes        │  │
                    │  └──────────────────────────────────────┘  │
                    └────────────────────────────────────────────┘
```

## 2. Sensor architecture

| Function | Part class | Interface | Notes |
|---|---|---|---|
| Soil moisture | Capacitive probe (non-corroding) | ADC | 0–100 % after dry/wet calibration |
| Soil pH | Glass electrode + high-Z amp | ADC | 2-point calibration (4.0/7.0), re-calibrate per season |
| Temperature | DS18B20 (buried) / SHT31 (air) | 1-Wire / I²C | ±0.3 °C |
| Electrical conductivity (optional) | 4-pin EC probe | ADC | salinity scope, not yet in the platform schema |
| N, P, K | **Not lab-grade on-probe** | — | use lab correlation or controlled baselines (see firmware doc §4); an EC sensor cannot output separate N/P/K |
| Battery | LiPo + divider | ADC | reported in `heartbeat.battery` |
| Positioning (optional) | NEO-6M GPS | UART | fills `location.latitude/longitude` |

## 3. Power architecture

- **Source**: single 18650 LiPo (2000–3000 mAh), optional 6 V / 1 W solar
  panel with TP4056 charging (field deployment only).
- **Regulation**: 3.3 V LDO (e.g. AMS1117-3.3) from battery; sensors powered
  from a switched load switch so probes are de-energized between readings.
- **Duty cycle**: ESP32 deep-sleep (~10–150 µA) → wake on RTC (5 min default)
  → read sensors → associate Wi-Fi → POST → sleep. One 30-second Wi-Fi
  session per 5 minutes at ~120 mA average ≈ **~8–12 days per charge**
  without solar (order-of-magnitude estimate; must be bench-measured).
- **Battery reporting**: `heartbeat.battery` % → platform raises a
  `BATTERY_LOW` advisory below 20 % (already implemented in `alerts.py`).

## 4. Communication architecture

| Layer | Today (prototype) | Later (field) |
|---|---|---|
| Transport | Wi-Fi → `HTTPS POST /api/iot/readings` | MQTT via broker → forwarder → same endpoint |
| Payload | JSON contract (see `docs/ESP32_FIRMWARE.md`) | **unchanged** |
| Auth | per-device token `X-Device-Token` | same token (or per-device TLS cert) |
| Offline behavior | NVS/SD store-and-forward | same queue, broker QoS 1 |
| Range beyond Wi-Fi | — | LoRa gateway → backhaul host running the forwarder |

The platform's quality pipeline, advisory alerts and dashboard are
transport-agnostic — they consume readings, not packets. That is the reason
HTTP is locked in first: it is the stable seam everything else migrates
behind.

## 5. Mechanical / enclosure (concept)

- IP67 enclosure, vented pressure-equalization membrane.
- 2–3 × 200 mm stainless stakes; probe heads potted with epoxy.
- Field ID label carrying `deviceId` (e.g. `SOIL-001`) and a QR code for
  registration (`POST /api/iot/devices`).

## 6. Concept BOM (order-of-magnitude, USD)

| Item | Qty | Est. |
|---|---|---|
| ESP32-C3 dev module | 1 | ~4 |
| Capacitive moisture probe | 1 | ~6 |
| pH electrode + amp board | 1 | ~12 |
| Temp/RH sensor | 1 | ~4 |
| LiPo 18650 + holder | 1 | ~7 |
| TP4056 + LDO + passives | 1 set | ~4 |
| IP67 enclosure + stakes | 1 | ~10 |
| **Total per unit (prototype)** | | **~45–60** |

## 7. Explicit non-goals (roadmap discipline)

Per the project roadmap, the following are **deliberately not built now**:
PCB fab/assembly, MQTT cluster, Kafka/microservices, production firmware,
device-provisioning PKI. They are engineering-roadmap items after the
competition milestone — the software pipeline they will feed is already live
and tested (see `README.md` → REST API + IoT contract).
