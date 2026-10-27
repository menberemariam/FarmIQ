"""SQLite persistence for soil test reports.

Stores every saved prediction with its inputs, the real model output
(fertility status, recommendations, crop suggestions) and a plot/field name,
so users can track nutrient changes across seasons.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    N REAL NOT NULL,
    P REAL NOT NULL,
    K REAL NOT NULL,
    ph REAL NOT NULL,
    crop TEXT,
    temperature REAL,
    humidity REAL,
    rainfall REAL,
    fertility_status TEXT NOT NULL,
    recommendations TEXT NOT NULL,
    crops TEXT,
    lang TEXT DEFAULT 'en'
);
CREATE INDEX IF NOT EXISTS idx_reports_plot ON reports (plot, created_at);

CREATE TABLE IF NOT EXISTS farmers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    region TEXT,
    lang TEXT DEFAULT 'en',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id INTEGER NOT NULL REFERENCES farmers(id),
    name TEXT NOT NULL,
    farm_size_ha REAL,
    latitude REAL,
    longitude REAL,
    previous_crop TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plots_farmer ON plots (farmer_id);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL UNIQUE REFERENCES reports(id),
    implemented_at TEXT,
    implementation_notes TEXT,
    expected_yield REAL,
    actual_yield REAL,
    yield_unit TEXT DEFAULT 'kg/ha',
    harvest_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS iot_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    plot_id INTEGER NOT NULL REFERENCES plots(id),
    sensors TEXT,
    firmware TEXT DEFAULT '0.1.0',
    token TEXT NOT NULL UNIQUE,
    battery REAL,
    signal REAL,
    last_seen_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS iot_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device INTEGER NOT NULL REFERENCES iot_devices(id),
    source TEXT NOT NULL DEFAULT 'iot_simulator',
    N REAL NOT NULL,
    P REAL NOT NULL,
    K REAL NOT NULL,
    ph REAL NOT NULL,
    moisture REAL NOT NULL,
    temperature REAL NOT NULL,
    latitude REAL,
    longitude REAL,
    quality TEXT NOT NULL,
    flags TEXT NOT NULL DEFAULT '[]',
    reading_ts TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_device ON iot_readings (device, reading_ts DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device INTEGER NOT NULL REFERENCES iot_devices(id),
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    value REAL,
    threshold REAL,
    reading_id INTEGER REFERENCES iot_readings(id),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_device_status ON alerts (device, status);

CREATE TABLE IF NOT EXISTS alert_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES alerts(id),
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_emails_status ON alert_emails (status, id);

CREATE TABLE IF NOT EXISTS fertilizer_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL UNIQUE,
    price_etb_per_kg REAL NOT NULL,
    note TEXT,
    updated_at TEXT NOT NULL
);

-- Opt-in samples for future Ethiopian retraining. This table is deliberately
-- separate from reports: report fertility_status/recommendations are model
-- outputs, never ground-truth labels. A sample is usable only after an
-- explicit lab/expert verification sets label_status and include_in_training.
CREATE TABLE IF NOT EXISTS training_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_hash TEXT NOT NULL UNIQUE,
    report_id INTEGER REFERENCES reports(id),
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    consent INTEGER NOT NULL DEFAULT 0,
    N REAL NOT NULL,
    P REAL NOT NULL,
    K REAL NOT NULL,
    ph REAL NOT NULL,
    input_units TEXT NOT NULL DEFAULT 'kg_ha',
    lab_N REAL,
    lab_P REAL,
    lab_K REAL,
    lab_ph REAL,
    crop TEXT,
    variety TEXT,
    region TEXT,
    season TEXT,
    agroecological_zone TEXT,
    soil_type TEXT,
    texture_class TEXT,
    soc_pct REAL,
    soc_class TEXT,
    woreda TEXT,
    label_status TEXT,
    label_source TEXT,
    label_confidence REAL,
    verified_by TEXT,
    verified_at TEXT,
    include_in_training INTEGER NOT NULL DEFAULT 0,
    dataset_version TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_training_samples_label
    ON training_samples (include_in_training, label_source);
CREATE INDEX IF NOT EXISTS idx_training_samples_zone
    ON training_samples (agroecological_zone, label_status);
"""

# Indicative demo prices (ETB/kg) seeded once per database. The note is shown
# with every cost estimate — these are placeholders, not market data.
_FERTILIZER_PRICE_DEFAULTS = {
    "urea": 65.0,
    "dap": 95.0,
    "potassium_sulfate": 110.0,
    "lime": 8.0,
    "sulfur": 35.0,
}
_FERTILIZER_PRICE_NOTE = (
    "Indicative demo price (ETB/kg) — replace with local market price."
)

# Columns added to `reports` after the original schema shipped. Each is applied
# with a guarded ALTER so databases created before Phase 1 migrate in place.
_REPORT_COLUMNS = {
    "lang": "ALTER TABLE reports ADD COLUMN lang TEXT DEFAULT 'en'",
    "farmer_id": "ALTER TABLE reports ADD COLUMN farmer_id INTEGER",
    "plot_id": "ALTER TABLE reports ADD COLUMN plot_id INTEGER",
    "source": "ALTER TABLE reports ADD COLUMN source TEXT DEFAULT 'manual'",
    # Phase 2: S/B soil tests + region so a saved report's cost estimate
    # recomputes with the same product selection as the report text.
    "sulfur_ppm": "ALTER TABLE reports ADD COLUMN sulfur_ppm REAL",
    "boron_ppm": "ALTER TABLE reports ADD COLUMN boron_ppm REAL",
    "region": "ALTER TABLE reports ADD COLUMN region TEXT",
    # Enhancement C: named variety so history/cost recompute with the
    # crop x region x variety bands the analysis used.
    "variety": "ALTER TABLE reports ADD COLUMN variety TEXT",
    # Enhancement D: Meher/Belg advisory N band used by the analysis.
    "season": "ALTER TABLE reports ADD COLUMN season TEXT",
    # Phase 3: agroecological zone so history/cost recompute with the zone
    # bands the analysis used (Dataset/agroecological_zones.json).
    "agroecological_zone": (
        "ALTER TABLE reports ADD COLUMN agroecological_zone TEXT"
    ),
    # Phase 3b: salinity/sodicity inputs so a saline-zone report's cost
    # estimate keeps the fertilizer-pause (gypsum/leaching) decision.
    "ec_e": "ALTER TABLE reports ADD COLUMN ec_e REAL",
    "esp": "ALTER TABLE reports ADD COLUMN esp REAL",
    # Enhancement E: optional SOC/texture conditioning context so history and
    # cost recompute use the same advisory bands as the original analysis.
    "texture_class": "ALTER TABLE reports ADD COLUMN texture_class TEXT",
    "soc_pct": "ALTER TABLE reports ADD COLUMN soc_pct REAL",
    "soc_class": "ALTER TABLE reports ADD COLUMN soc_class TEXT",
    "woreda": "ALTER TABLE reports ADD COLUMN woreda TEXT",
}

# Additive columns on training_samples. The table itself is new, but guarded
# ALTERs keep databases created by an earlier build of this pipeline usable.
_TRAINING_COLUMNS = {
    "lab_N": "ALTER TABLE training_samples ADD COLUMN lab_N REAL",
    "lab_P": "ALTER TABLE training_samples ADD COLUMN lab_P REAL",
    "lab_K": "ALTER TABLE training_samples ADD COLUMN lab_K REAL",
    "lab_ph": "ALTER TABLE training_samples ADD COLUMN lab_ph REAL",
}


def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _connect()
    conn.executescript(_SCHEMA)
    for column, alter_sql in _REPORT_COLUMNS.items():
        try:
            exists = conn.execute(
                "SELECT COUNT(*) FROM pragma_table_info('reports') WHERE name = ?",
                (column,),
            ).fetchone()[0]
            if exists == 0:
                conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass
    for column, alter_sql in _TRAINING_COLUMNS.items():
        try:
            exists = conn.execute(
                "SELECT COUNT(*) FROM pragma_table_info('training_samples') "
                "WHERE name = ?", (column,),
            ).fetchone()[0]
            if exists == 0:
                conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass
    # Created after the column migration (older DBs lack farmer_id until now).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_farmer ON reports (farmer_id)"
    )
    # Seed indicative prices on first init only. init_db() runs before EVERY
    # db helper, so this must stay a pure read on existing databases — a write
    # here would add a fsync to every single operation (and stall heavy paths
    # like demo backfills that call helpers hundreds of times).
    if conn.execute(
        "SELECT COUNT(*) FROM fertilizer_prices"
    ).fetchone()[0] == 0:
        for product, price in _FERTILIZER_PRICE_DEFAULTS.items():
            conn.execute(
                """INSERT OR IGNORE INTO fertilizer_prices
                   (product, price_etb_per_kg, note, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (product, price, _FERTILIZER_PRICE_NOTE,
                 datetime.now(timezone.utc).isoformat()),
            )
    conn.commit()
    conn.close()


def list_fertilizer_prices():
    """All fertilizer price rows (product, ETB/kg, note), sorted by product."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT product, price_etb_per_kg, note, updated_at
               FROM fertilizer_prices ORDER BY product"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_report(plot, N, P, K, ph, fertility_status, recommendations,
                crop=None, temperature=None, humidity=None, rainfall=None,
                crops=None, lang="en", farmer_id=None, plot_id=None,
                source="manual", sulfur_ppm=None, boron_ppm=None, region=None,
                variety=None, agroecological_zone=None, ec_e=None, esp=None,
                season=None, texture_class=None, soc_pct=None, soc_class=None,
                woreda=None):
    """Persist one real prediction. Returns the new report id."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO reports
               (plot, created_at, N, P, K, ph, crop, temperature, humidity, rainfall,
                fertility_status, recommendations, crops, lang, farmer_id, plot_id, source,
                sulfur_ppm, boron_ppm, region, variety, season,
                agroecological_zone, ec_e, esp, texture_class, soc_pct, soc_class,
                woreda)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plot.strip(),
                datetime.now(timezone.utc).isoformat(),
                N, P, K, ph,
                crop,
                temperature, humidity, rainfall,
                fertility_status,
                json.dumps(recommendations),
                json.dumps(crops) if crops is not None else None,
                lang,
                farmer_id, plot_id, source,
                sulfur_ppm, boron_ppm, region, variety, season,
                agroecological_zone, ec_e, esp,
                texture_class, soc_pct, soc_class, woreda,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_plots():
    """All plot names that have at least one report (most recent first)."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT plot, COUNT(*) as n, MAX(created_at) as last
               FROM reports GROUP BY plot ORDER BY last DESC"""
        ).fetchall()
        return [{"plot": r["plot"], "count": r["n"], "last": r["last"]} for r in rows]
    finally:
        conn.close()


def list_reports(plot=None, limit=20, farmer_id=None):
    """Reports ordered newest-first, optionally filtered by plot and/or farmer."""
    init_db()
    conn = _connect()
    try:
        where, params = [], []
        if plot:
            where.append("plot = ?")
            params.append(plot)
        if farmer_id is not None:
            where.append("farmer_id = ?")
            params.append(farmer_id)
        sql = "SELECT * FROM reports"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        reports = []
        for r in rows:
            report = dict(r)
            report["recommendations"] = json.loads(r["recommendations"])
            report["crops"] = json.loads(r["crops"]) if r["crops"] else None
            reports.append(report)
        return reports
    finally:
        conn.close()


# ---------------- Training samples (opt-in, real Ethiopian data) ----------


def _training_text(value):
    return str(value).strip().lower() if value is not None else ""


def training_sample_hash(N, P, K, ph, crop=None, variety=None, region=None,
                         season=None, agroecological_zone=None,
                         soil_type=None, texture_class=None, soc_pct=None,
                         soc_class=None, woreda=None, input_units="kg_ha"):
    """Stable content hash for deduplicating identical labelled samples."""
    payload = {
        "N": round(float(N), 4),
        "P": round(float(P), 4),
        "K": round(float(K), 4),
        "ph": round(float(ph), 4),
        "input_units": _training_text(input_units),
        "crop": _training_text(crop),
        "variety": _training_text(variety),
        "region": _training_text(region),
        "season": _training_text(season),
        "zone": _training_text(agroecological_zone),
        "soil_type": _training_text(soil_type),
        "texture_class": _training_text(texture_class),
        "soc_pct": (round(float(soc_pct), 4)
                    if soc_pct is not None else None),
        "soc_class": _training_text(soc_class),
        "woreda": _training_text(woreda),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def save_training_sample(N, P, K, ph, report_id=None, source="manual",
                         consent=False, input_units="kg_ha", crop=None,
                         variety=None, region=None, season=None,
                         agroecological_zone=None, soil_type=None,
                         texture_class=None, soc_pct=None, soc_class=None,
                         woreda=None, label_status=None, label_source=None,
                         label_confidence=None, verified_by=None,
                         include_in_training=False, dataset_version=None):
    """Insert one consented sample. Returns its id, or None without consent.

    Duplicate content (same hash) returns the existing row id without
    overwriting any verification already attached to it.
    """
    if not consent:
        return None
    init_db()
    sample_hash = training_sample_hash(
        N, P, K, ph, crop=crop, variety=variety, region=region,
        season=season, agroecological_zone=agroecological_zone,
        soil_type=soil_type, texture_class=texture_class, soc_pct=soc_pct,
        soc_class=soc_class, woreda=woreda, input_units=input_units)
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO training_samples
               (sample_hash, report_id, created_at, source, consent, N, P, K,
                ph, input_units, crop, variety, region, season,
                agroecological_zone, soil_type, texture_class, soc_pct,
                soc_class, woreda, label_status, label_source,
                label_confidence, verified_by, verified_at,
                include_in_training, dataset_version, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(sample_hash) DO NOTHING""",
            (sample_hash, report_id, now, source, int(bool(consent)),
             N, P, K, ph, input_units, crop, variety, region, season,
             agroecological_zone, soil_type, texture_class, soc_pct,
             soc_class, woreda, label_status, label_source,
             label_confidence, verified_by,
             now if label_status is not None else None,
             int(bool(include_in_training)), dataset_version),
        )
        conn.commit()
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT id FROM training_samples WHERE sample_hash = ?",
                (sample_hash,)).fetchone()
            return row["id"] if row else None
        return cur.lastrowid
    finally:
        conn.close()


def get_training_sample(sample_id):
    """One training sample as a dict, or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM training_samples WHERE id = ?", (sample_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_training_sample(report_id=None, sample_hash=None):
    """Look up a sample by report id or content hash, or None."""
    if report_id is None and sample_hash is None:
        return None
    init_db()
    conn = _connect()
    try:
        if sample_hash is not None:
            row = conn.execute(
                "SELECT * FROM training_samples WHERE sample_hash = ?",
                (sample_hash,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM training_samples WHERE report_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (report_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_training_sample(sample_id, label_status, label_source,
                           label_confidence=None, verified_by=None,
                           include_in_training=True, dataset_version=None,
                           lab_N=None, lab_P=None, lab_K=None, lab_ph=None):
    """Attach a verified lab/expert label. Returns the updated row or None.

    Optional lab_* values replace the user-entered measurements used for
    training while leaving the original sample row auditable.
    """
    if label_status not in ("Good", "Moderate", "Poor"):
        raise ValueError("label_status must be Good, Moderate or Poor")
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            """UPDATE training_samples
               SET label_status = ?, label_source = ?, label_confidence = ?,
                   verified_by = ?, verified_at = ?,
                   include_in_training = ?, dataset_version = ?,
                   lab_N = COALESCE(?, lab_N), lab_P = COALESCE(?, lab_P),
                   lab_K = COALESCE(?, lab_K), lab_ph = COALESCE(?, lab_ph)
               WHERE id = ?""",
            (label_status, label_source, label_confidence, verified_by, now,
             int(bool(include_in_training)), dataset_version,
             lab_N, lab_P, lab_K, lab_ph, sample_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return get_training_sample(sample_id)
    finally:
        conn.close()


def update_training_sample_context(sample_id, agroecological_zone=None,
                                   soil_type=None, crop=None, variety=None,
                                   region=None, season=None,
                                   texture_class=None, soc_pct=None,
                                   soc_class=None, woreda=None):
    """Fill missing Ethiopian context from a verified lab record.

    Only non-None arguments are written. The content hash is recomputed so
    an identical later sample still deduplicates correctly.
    """
    sample = get_training_sample(sample_id)
    if sample is None:
        return None
    merged = {key: sample.get(key) for key in (
        "N", "P", "K", "ph", "input_units", "crop", "variety", "region",
        "season", "agroecological_zone", "soil_type", "texture_class",
        "soc_pct", "soc_class", "woreda")}
    overrides = {
        "agroecological_zone": agroecological_zone,
        "soil_type": soil_type,
        "crop": crop,
        "variety": variety,
        "region": region,
        "season": season,
        "texture_class": texture_class,
        "soc_pct": soc_pct,
        "soc_class": soc_class,
        "woreda": woreda,
    }
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    sample_hash = training_sample_hash(
        merged["N"], merged["P"], merged["K"], merged["ph"],
        crop=merged["crop"], variety=merged["variety"],
        region=merged["region"], season=merged["season"],
        agroecological_zone=merged["agroecological_zone"],
        soil_type=merged["soil_type"],
        texture_class=merged["texture_class"], soc_pct=merged["soc_pct"],
        soc_class=merged["soc_class"], woreda=merged["woreda"],
        input_units=merged["input_units"])
    init_db()
    conn = _connect()
    try:
        try:
            conn.execute(
                """UPDATE training_samples
                   SET sample_hash = ?, agroecological_zone = ?,
                       soil_type = ?, crop = ?, variety = ?, region = ?,
                       season = ?, texture_class = ?, soc_pct = ?,
                       soc_class = ?, woreda = ?
                   WHERE id = ?""",
                (sample_hash, merged["agroecological_zone"],
                 merged["soil_type"], merged["crop"], merged["variety"],
                 merged["region"], merged["season"], merged["texture_class"],
                 merged["soc_pct"], merged["soc_class"], merged["woreda"],
                 sample_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Another identical sample already exists; keep this row's
            # existing context rather than merging into it silently.
            conn.rollback()
        row = conn.execute(
            "SELECT * FROM training_samples WHERE id = ?", (sample_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_training_samples(include_in_training=None, label_source=None,
                          only_verified=False, limit=1000):
    """Training samples, newest first, with optional label filters."""
    init_db()
    where, params = [], []
    if include_in_training is not None:
        where.append("include_in_training = ?")
        params.append(int(bool(include_in_training)))
    if label_source is not None:
        where.append("label_source = ?")
        params.append(label_source)
    if only_verified:
        where.append("label_status IS NOT NULL")
        where.append("consent = 1")
    sql = "SELECT * FROM training_samples"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    conn = _connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def training_sample_counts():
    """Counts used to decide whether a real-data retrain has enough support."""
    init_db()
    conn = _connect()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM training_samples").fetchone()[0]
        consented = conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE consent = 1"
        ).fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM training_samples "
            "WHERE consent = 1 AND label_status IS NOT NULL").fetchone()[0]
        included = conn.execute(
            "SELECT COUNT(*) FROM training_samples "
            "WHERE consent = 1 AND include_in_training = 1 "
            "AND label_status IS NOT NULL").fetchone()[0]
        by_label = {
            row["label_status"]: row["n"] for row in conn.execute(
                """SELECT label_status, COUNT(*) AS n FROM training_samples
                   WHERE include_in_training = 1 AND label_status IS NOT NULL
                   GROUP BY label_status""").fetchall()
        }
        by_zone = {
            (row["agroecological_zone"] or "unknown"): row["n"]
            for row in conn.execute(
                """SELECT agroecological_zone, COUNT(*) AS n
                   FROM training_samples
                   WHERE include_in_training = 1
                     AND label_status IS NOT NULL
                   GROUP BY agroecological_zone""").fetchall()
        }
        by_source = {
            (row["label_source"] or "unknown"): row["n"]
            for row in conn.execute(
                """SELECT label_source, COUNT(*) AS n FROM training_samples
                   WHERE include_in_training = 1 AND label_status IS NOT NULL
                   GROUP BY label_source""").fetchall()
        }
        return {
            "total": total,
            "consented": consented,
            "verified": verified,
            "included": included,
            "by_label_status": by_label,
            "by_zone": by_zone,
            "by_label_source": by_source,
        }
    finally:
        conn.close()


# ---------------- Farmers / plots / outcomes (agent flow) ----------------


def create_farmer(name, phone=None, region=None, lang="en"):
    """Register a farmer. Returns the new farmer id."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO farmers (name, phone, region, lang, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (name.strip(), phone, region, lang, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_farmer(farmer_id):
    """One farmer row as a dict, or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_farmer_summaries():
    """All farmers (newest first) with plot/report counts and last test date."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT f.*, COUNT(DISTINCT p.id) AS plot_count,
                      COUNT(DISTINCT r.id) AS report_count,
                      MAX(r.created_at) AS last_report_at
               FROM farmers f
               LEFT JOIN plots p ON p.farmer_id = f.id
               LEFT JOIN reports r ON r.farmer_id = f.id
               GROUP BY f.id
               ORDER BY f.created_at DESC, f.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_plot(farmer_id, name, farm_size_ha=None, latitude=None,
                longitude=None, previous_crop=None):
    """Register a plot under a farmer. Returns the new plot id."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO plots
               (farmer_id, name, farm_size_ha, latitude, longitude, previous_crop, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (farmer_id, name.strip(), farm_size_ha, latitude, longitude,
             previous_crop, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_plot(plot_id):
    """One plot row as a dict, or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM plots WHERE id = ?", (plot_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_farmer_plots(farmer_id):
    """Plots of a farmer (oldest first) with report count and latest status."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT p.*, COUNT(r.id) AS report_count,
                      MAX(r.created_at) AS last_report_at,
                      (SELECT r2.fertility_status FROM reports r2
                       WHERE r2.plot_id = p.id
                       ORDER BY r2.created_at DESC, r2.id DESC LIMIT 1) AS latest_status
               FROM plots p
               LEFT JOIN reports r ON r.plot_id = p.id
               WHERE p.farmer_id = ?
               GROUP BY p.id
               ORDER BY p.created_at ASC, p.id ASC""",
            (farmer_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_report(report_id):
    """One report row as a dict (recommendations/crops parsed), or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        report = dict(row)
        report["recommendations"] = json.loads(row["recommendations"])
        report["crops"] = json.loads(row["crops"]) if row["crops"] else None
        return report
    finally:
        conn.close()


def get_outcome(report_id):
    """The outcome row for a report, or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM outcomes WHERE report_id = ?", (report_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_outcome(report_id, implementation_notes=None, implemented_at=None,
                   expected_yield=None, actual_yield=None, yield_unit="kg/ha",
                   harvest_at=None):
    """Create or replace the outcome for one report. Returns the saved row."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO outcomes
               (report_id, implemented_at, implementation_notes, expected_yield,
                actual_yield, yield_unit, harvest_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(report_id) DO UPDATE SET
                 implemented_at = excluded.implemented_at,
                 implementation_notes = excluded.implementation_notes,
                 expected_yield = excluded.expected_yield,
                 actual_yield = excluded.actual_yield,
                 yield_unit = excluded.yield_unit,
                 harvest_at = excluded.harvest_at,
                 updated_at = excluded.updated_at""",
            (report_id, implemented_at, implementation_notes, expected_yield,
             actual_yield, yield_unit, harvest_at, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM outcomes WHERE report_id = ?", (report_id,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def farmer_detail(farmer_id):
    """Farmer + plots + reports (each with its outcome attached), or None."""
    farmer = get_farmer(farmer_id)
    if farmer is None:
        return None
    reports = list_reports(farmer_id=farmer_id, limit=50)
    for report in reports:
        report["outcome"] = get_outcome(report["id"])
    return {
        "farmer": farmer,
        "plots": list_farmer_plots(farmer_id),
        "reports": reports,
    }


# ---------------- IoT devices / readings (Phase 2) ----------------

DEFAULT_SENSORS = ["ph", "npk", "moisture", "temperature"]


def register_device(device_id, name, plot_id, token, sensors=None,
                    firmware="0.1.0", battery=None, signal=None):
    """Register a (virtual or future physical) soil sensor device."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO iot_devices
               (device_id, name, plot_id, sensors, firmware, token,
                battery, signal, last_seen_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                device_id, name, plot_id,
                json.dumps(sensors or DEFAULT_SENSORS), firmware, token,
                battery, signal, datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_device(device_id):
    """One device row by its public device_id string (sensors parsed), or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM iot_devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if row is None:
            return None
        device = dict(row)
        device["sensors"] = json.loads(row["sensors"]) if row["sensors"] else []
        return device
    finally:
        conn.close()


def list_devices():
    """All devices (oldest first), sensors parsed."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM iot_devices ORDER BY created_at ASC, id ASC"
        ).fetchall()
        devices = []
        for row in rows:
            device = dict(row)
            device["sensors"] = json.loads(row["sensors"]) if row["sensors"] else []
            devices.append(device)
        return devices
    finally:
        conn.close()


def touch_device(device_row_id, seen_at, battery=None, signal=None):
    """Update heartbeat: last seen (+ optional battery/signal) after an ingest."""
    init_db()
    conn = _connect()
    try:
        sets, params = ["last_seen_at = ?"], [seen_at]
        if battery is not None:
            sets.append("battery = ?")
            params.append(battery)
        if signal is not None:
            sets.append("signal = ?")
            params.append(signal)
        params.append(device_row_id)
        conn.execute(
            f"UPDATE iot_devices SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()
    finally:
        conn.close()


def insert_reading(device_row_id, source, N, P, K, ph, moisture, temperature,
                   quality, flags, reading_ts, latitude=None, longitude=None):
    """Store one validated sensor reading. Returns the new reading id."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO iot_readings
               (device, source, N, P, K, ph, moisture, temperature,
                latitude, longitude, quality, flags, reading_ts, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                device_row_id, source, N, P, K, ph, moisture, temperature,
                latitude, longitude, quality, json.dumps(flags), reading_ts,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _parse_reading(row):
    reading = dict(row)
    reading["flags"] = json.loads(row["flags"]) if row["flags"] else []
    return reading


def list_readings(device_row_id, limit=30):
    """Readings for one device, newest first."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT * FROM iot_readings WHERE device = ?
               ORDER BY reading_ts DESC, id DESC LIMIT ?""",
            (device_row_id, limit),
        ).fetchall()
        return [_parse_reading(r) for r in rows]
    finally:
        conn.close()


def latest_reading(device_row_id):
    """Most recent reading for a device, or None."""
    rows = list_readings(device_row_id, limit=1)
    return rows[0] if rows else None


def get_reading(reading_id):
    """One reading joined with its device's public id and plot_id, or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT r.*, d.device_id AS device_public_id, d.plot_id AS plot_id
               FROM iot_readings r
               JOIN iot_devices d ON d.id = r.device
               WHERE r.id = ?""",
            (reading_id,),
        ).fetchone()
        return _parse_reading(row) if row else None
    finally:
        conn.close()


def find_reading(device_row_id, reading_ts):
    """The stored reading for (device, exact timestamp), or None.

    Powers idempotent ingest replays: the same payload re-sent (same device,
    same normalized timestamp) maps to the original row instead of a duplicate.
    """
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT id, quality, flags FROM iot_readings
               WHERE device = ? AND reading_ts = ?
               ORDER BY id LIMIT 1""",
            (device_row_id, reading_ts),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "quality": row["quality"],
            "flags": json.loads(row["flags"]) if row["flags"] else [],
        }
    finally:
        conn.close()


def prune_readings(device_row_id, keep=1000):
    """Keep only the newest `keep` readings for a device (demo runs must not grow unbounded)."""
    init_db()
    conn = _connect()
    try:
        conn.execute(
            """DELETE FROM iot_readings
               WHERE device = ? AND id NOT IN (
                   SELECT id FROM iot_readings WHERE device = ?
                   ORDER BY reading_ts DESC, id DESC LIMIT ?
               )""",
            (device_row_id, device_row_id, keep),
        )
        conn.commit()
    finally:
        conn.close()


def _alert_join_row(row):
    """Alert row + the device/plot/farmer context the UI deep-links with."""
    alert = dict(row)
    for key in ("device_public_id", "device_name", "plot_id", "farmer_id"):
        alert.setdefault(key, row[key] if key in row.keys() else None)
    return alert


_ALERT_SELECT = """SELECT a.*, d.device_id AS device_public_id,
           d.name AS device_name, d.plot_id AS plot_id, p.farmer_id AS farmer_id
    FROM alerts a
    JOIN iot_devices d ON d.id = a.device
    LEFT JOIN plots p ON p.id = d.plot_id"""


def create_alert(device_row_id, alert_type, severity, value, threshold,
                 reading_id, message):
    """Open one alert (the engine enforces one active per device+type)."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO alerts
               (device, alert_type, severity, status, value, threshold,
                reading_id, message, created_at, resolved_at, resolved_reason)
               VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL, NULL)""",
            (
                device_row_id, alert_type, severity, value, threshold,
                reading_id, message, datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_active_alert(device_row_id, alert_type):
    """The one active alert of this type for this device, or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            f"{_ALERT_SELECT} WHERE a.device = ? AND a.status = 'active'"
            " AND a.alert_type = ?",
            (device_row_id, alert_type),
        ).fetchone()
        return _alert_join_row(row) if row else None
    finally:
        conn.close()


def resolve_active_alert(device_row_id, alert_type, reason="auto"):
    """Auto-resolve a device's active alert of one type (condition cleared)."""
    init_db()
    conn = _connect()
    try:
        conn.execute(
            """UPDATE alerts SET status = 'resolved', resolved_at = ?,
               resolved_reason = ?
               WHERE device = ? AND alert_type = ? AND status = 'active'""",
            (
                datetime.now(timezone.utc).isoformat(), reason,
                device_row_id, alert_type,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def ack_alert(alert_id):
    """Agent acknowledges an active alert. Returns the joined row, or None
    if the id is unknown or the alert was already resolved."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """UPDATE alerts SET status = 'resolved', resolved_at = ?,
               resolved_reason = 'acked'
               WHERE id = ? AND status = 'active'""",
            (datetime.now(timezone.utc).isoformat(), alert_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            f"{_ALERT_SELECT} WHERE a.id = ?", (alert_id,)
        ).fetchone()
        return _alert_join_row(row)
    finally:
        conn.close()


def list_alerts(device_row_id=None, status=None, limit=50):
    """Alerts newest-first, optionally filtered, joined with deep-link context."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""{_ALERT_SELECT}
                WHERE (? IS NULL OR a.device = ?)
                  AND (? IS NULL OR a.status = ?)
                ORDER BY a.created_at DESC, a.id DESC LIMIT ?""",
            (device_row_id, device_row_id, status, status, limit),
        ).fetchall()
        return [_alert_join_row(r) for r in rows]
    finally:
        conn.close()


def get_alert(alert_id):
    """One alert by id (joined with deep-link context), or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            f"{_ALERT_SELECT} WHERE a.id = ?", (alert_id,)
        ).fetchone()
        return _alert_join_row(row) if row else None
    finally:
        conn.close()


def count_active_alerts(device_row_id=None):
    """Number of active alerts (badge count), optionally for one device."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT COUNT(*) FROM alerts
               WHERE status = 'active' AND (? IS NULL OR device = ?)""",
            (device_row_id, device_row_id),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def enqueue_alert_email(alert_id, to_addr, subject, body):
    """Queue one notification email for an alert (outbox pattern)."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO alert_emails
               (alert_id, to_addr, subject, body, status, attempts,
                last_error, created_at, sent_at)
               VALUES (?, ?, ?, ?, 'queued', 0, NULL, ?, NULL)""",
            (
                alert_id, to_addr, subject, body,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def next_queued_email():
    """Oldest queued outbox row, or None."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM alert_emails WHERE status = 'queued'"
            " ORDER BY id ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_email_status(email_id, status, attempts, error=None):
    """Mark an outbox row sent/failed with its attempt count + last error."""
    init_db()
    conn = _connect()
    try:
        sent_at = (
            datetime.now(timezone.utc).isoformat() if status == "sent" else None
        )
        conn.execute(
            """UPDATE alert_emails SET status = ?, attempts = ?,
               last_error = ?, sent_at = ? WHERE id = ?""",
            (status, attempts, error, sent_at, email_id),
        )
        conn.commit()
    finally:
        conn.close()


def clear_readings(device_row_id):
    """Delete every reading for a device (demo-run reset)."""
    init_db()
    conn = _connect()
    try:
        conn.execute("DELETE FROM iot_readings WHERE device = ?", (device_row_id,))
        conn.commit()
    finally:
        conn.close()


def latest_two(plot):
    """The two most recent reports for a plot (newest first), for delta tracking."""
    reports = list_reports(plot=plot, limit=2)
    if len(reports) < 2:
        return None
    latest, previous = reports[0], reports[1]
    deltas = {}
    for col in ("N", "P", "K", "ph"):
        deltas[col] = round(latest[col] - previous[col], 2)
    old_status = previous["fertility_status"]
    new_status = latest["fertility_status"]
    return {
        "latest": latest,
        "previous": previous,
        "deltas": deltas,
        "status_changed": old_status != new_status,
        "old_status": old_status,
        "new_status": new_status,
    }


def cleanup(path=None):
    """Delete the database file (used by tests to start clean)."""
    p = Path(path or config.DB_PATH)
    if p.exists():
        p.unlink()
