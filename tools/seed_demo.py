#!/usr/bin/env python3
"""Seed the demo farmer used in presentations and screenshots.

Creates Abebe Getachew (Oromia-Arsi) with a 2.1 ha plot, two real soil
tests (Poor → Good), a harvest outcome and the SOIL-001 demo device, so
the agent portal, the report-improvement flow and the IoT dashboard have
believable data out of the box.

Usage:
    python3 tools/seed_demo.py               # seed config.DB_PATH
    python3 tools/seed_demo.py --db other.db # seed a specific database
    python3 tools/seed_demo.py --force       # wipe the demo farmer first

Idempotent: running it twice does nothing unless --force is given.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import db  # noqa: E402

DEMO_FARMER_NAME = "Abebe Getachew"
DEMO_REGION = "Oromia-Arsi"

# Two real measurements: run 1 is a depleted field, run 2 after amending.
# Values are within the EthioSIS kg/ha bands so the amended re-test scores
# Good under the current labeling (P band 78-117 kg/ha after the 2.6 factor).
# The plot sits in the Oromia-Arsi highlands -> Highland Nitisols zone.
DEMO_REPORTS = [
    {
        "N": 25.0, "P": 10.0, "K": 40.0, "ph": 5.5, "crop": "maize",
        "agroecological_zone": "highland_nitisols",
        "created_at": "2026-05-10T09:00:00+00:00",
    },
    {
        "N": 80.0, "P": 90.0, "K": 120.0, "ph": 6.8, "crop": "maize",
        "agroecological_zone": "highland_nitisols",
        "created_at": "2026-09-15T09:00:00+00:00",
    },
]

DEMO_OUTCOME = {
    "implementation_notes": "Lime (2 t/ha) in May + urea split (130 kg/ha)",
    "implemented_at": "2026-05-20",
    "expected_yield": 3500.0,
    "actual_yield": 3200.0,
    "yield_unit": "kg/ha",
    "harvest_at": "2026-09-10",
}

# Demo soil sensor: bound to North Ridge with a deterministic token so docs,
# tests and demos can use it verbatim (not a secret — demo data only).
DEMO_DEVICE_ID = "SOIL-001"
DEMO_DEVICE_NAME = "Oromia Soil Prototype 01"
DEMO_DEVICE_TOKEN = "soil001-demo-token"


def _fertility_status(N, P, K, ph):
    import joblib
    import pandas as pd

    model = joblib.load(config.SOIL_MODEL_PKL)
    row = pd.DataFrame([{"N": N, "P": P, "K": K, "ph": ph}])
    return str(model.predict(row)[0])


def _recommendations(N, P, K, ph, crop, zone=None):
    from ML.engine import analyze_soil, recommend_soil

    analysis = analyze_soil(N, P, K, ph, crop=crop, zone=zone)
    return recommend_soil(analysis, lang="en", zone=zone)


def _find_demo_farmer(conn):
    return conn.execute(
        "SELECT id FROM farmers WHERE name = ? AND region = ?",
        (DEMO_FARMER_NAME, DEMO_REGION),
    ).fetchone()


def _wipe_demo_farmer(conn, farmer_id):
    """Remove the demo farmer's reports (with outcomes), plots and the farmer."""
    report_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM reports WHERE farmer_id = ?", (farmer_id,)
        ).fetchall()
    ]
    for rid in report_ids:
        conn.execute("DELETE FROM outcomes WHERE report_id = ?", (rid,))
    conn.execute("DELETE FROM reports WHERE farmer_id = ?", (farmer_id,))
    conn.execute("DELETE FROM plots WHERE farmer_id = ?", (farmer_id,))
    conn.execute("DELETE FROM farmers WHERE id = ?", (farmer_id,))


def _wipe_demo_device(conn):
    """Remove the demo device and its readings (used by --force)."""
    row = conn.execute(
        "SELECT id FROM iot_devices WHERE device_id = ?", (DEMO_DEVICE_ID,)
    ).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM iot_readings WHERE device = ?", (row["id"],))
    conn.execute("DELETE FROM iot_devices WHERE id = ?", (row["id"],))


def _ensure_device(conn, farmer_id):
    """Register SOIL-001 on the farmer's first plot; idempotent.

    Returns a message fragment to append to the seed summary.
    """
    if db.get_device(DEMO_DEVICE_ID) is not None:
        return ""
    plots = db.list_farmer_plots(farmer_id)
    if not plots:
        return ""
    # Release this connection's locks before db.register_device opens its own.
    conn.commit()
    db.register_device(
        DEMO_DEVICE_ID, DEMO_DEVICE_NAME, plots[0]["id"], DEMO_DEVICE_TOKEN,
        battery=87.0, signal=-61.0,
    )
    return f", demo device '{DEMO_DEVICE_ID}' (token '{DEMO_DEVICE_TOKEN}')"


def seed(force=False, db_path=None):
    """Seed the demo data. Returns (created, message)."""
    if db_path is not None:
        config.DB_PATH = Path(db_path)
    db.init_db()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if force:
            _wipe_demo_device(conn)
            conn.commit()

        existing = _find_demo_farmer(conn)
        if existing is not None:
            if not force:
                return False, (
                    f"Demo farmer '{DEMO_FARMER_NAME}' already exists "
                    f"(farmer id {existing['id']}). Use --force to reseed"
                    + _ensure_device(conn, existing["id"]) + "."
                )
            _wipe_demo_farmer(conn, existing["id"])
            conn.commit()

        farmer_id = db.create_farmer(
            DEMO_FARMER_NAME, phone="0911234567", region=DEMO_REGION, lang="en"
        )
        plot_id = db.create_plot(
            farmer_id, "North Ridge", farm_size_ha=2.1,
            latitude=7.95, longitude=39.13, previous_crop="maize",
        )

        first_report_id = None
        for spec in DEMO_REPORTS:
            status = _fertility_status(spec["N"], spec["P"], spec["K"], spec["ph"])
            recs = _recommendations(
                spec["N"], spec["P"], spec["K"], spec["ph"], spec["crop"],
                zone=spec.get("agroecological_zone"),
            )
            rid = db.save_report(
                "North Ridge", spec["N"], spec["P"], spec["K"], spec["ph"],
                status, recs, crop=spec["crop"], farmer_id=farmer_id,
                plot_id=plot_id, source="manual",
                agroecological_zone=spec.get("agroecological_zone"),
            )
            # Backdate so the history reads as a real before/after timeline.
            conn.execute(
                "UPDATE reports SET created_at = ? WHERE id = ?",
                (spec["created_at"], rid),
            )
            # Release this connection's write lock before db.save_report
            # opens its own connection for the next insert.
            conn.commit()
            if first_report_id is None:
                first_report_id = rid
        conn.commit()

        db.upsert_outcome(first_report_id, **DEMO_OUTCOME)
        return True, (
            f"Seeded '{DEMO_FARMER_NAME}' (farmer id {farmer_id}), plot "
            f"'North Ridge' (2.1 ha), {len(DEMO_REPORTS)} soil tests and 1 "
            f"outcome{_ensure_device(conn, farmer_id)}."
        )
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="SQLite database to seed (default: config.DB_PATH)")
    parser.add_argument(
        "--force", action="store_true",
        help="delete the demo farmer first, then reseed",
    )
    args = parser.parse_args(argv)

    created, message = seed(force=args.force, db_path=args.db)
    print(("Seeded: " if created else "Skipped: ") + message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
