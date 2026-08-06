"""SQLite persistence for soil test reports.

Stores every saved prediction with its inputs, the real model output
(fertility status, recommendations, crop suggestions) and a plot/field name,
so users can track nutrient changes across seasons.
"""

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
    crops TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_plot ON reports (plot, created_at);
"""


def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _connect()
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()


def save_report(plot, N, P, K, ph, fertility_status, recommendations,
                crop=None, temperature=None, humidity=None, rainfall=None, crops=None):
    """Persist one real prediction. Returns the new report id."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO reports
               (plot, created_at, N, P, K, ph, crop, temperature, humidity, rainfall,
                fertility_status, recommendations, crops)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plot.strip(),
                datetime.now(timezone.utc).isoformat(),
                N, P, K, ph,
                crop,
                temperature, humidity, rainfall,
                fertility_status,
                json.dumps(recommendations),
                json.dumps(crops) if crops is not None else None,
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


def list_reports(plot=None, limit=20):
    """Reports ordered newest-first, optionally filtered by plot."""
    init_db()
    conn = _connect()
    try:
        if plot:
            rows = conn.execute(
                "SELECT * FROM reports WHERE plot = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (plot, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reports ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        reports = []
        for r in rows:
            report = dict(r)
            report["recommendations"] = json.loads(r["recommendations"])
            report["crops"] = json.loads(r["crops"]) if r["crops"] else None
            reports.append(report)
        return reports
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
