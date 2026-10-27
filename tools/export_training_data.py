"""Export consented, verified Ethiopian samples for model training.

Only samples with include_in_training=1, consent=1 and a verified
Good/Moderate/Poor label are exported. LLM text and report model outputs are
never read: the label must have been attached by import_lab_results.py.

Every real row also carries ``woreda`` and ``site_id``. ``site_id`` is the
spatial grouping unit (woreda, else region, else report/sample id) so
``SOIL_GROUP_COL=site_id`` cannot leak neighbouring samples into the test fold;
synthetic rows merged with ``--combine-with`` get ``synthetic:<zone>`` so they
never share a group with real rows.

Examples:
    python3 tools/export_training_data.py
    python3 tools/export_training_data.py --out Dataset/real_v1.csv --force
    python3 tools/export_training_data.py --combine-with \
        Dataset/soil_data_ethio_v6.csv --combined-out \
        Dataset/soil_data_ethio_v7.csv --force
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

VALID_STATUSES = ("Good", "Moderate", "Poor")
SCHEMA_VERSION = 1


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None,
                        help="SQLite DB path (default: config.DB_PATH)")
    parser.add_argument("--out", type=Path, default=config.TRAINING_REAL_CSV)
    parser.add_argument("--meta", type=Path, default=None)
    parser.add_argument("--label-source", action="append", default=None,
                        help="Only export these label sources (repeatable)")
    parser.add_argument("--require-zone", action="store_true", default=True)
    parser.add_argument("--allow-missing-zone", dest="require_zone",
                        action="store_false")
    parser.add_argument("--require-soil-type", action="store_true",
                        default=True)
    parser.add_argument("--allow-missing-soil-type", dest="require_soil_type",
                        action="store_false")
    parser.add_argument("--min-per-class", type=int, default=0)
    parser.add_argument("--combine-with", type=Path, default=None,
                        help="Synthetic/previous CSV to merge (e.g. v6)")
    parser.add_argument("--combined-out", type=Path,
                        default=config.TRAINING_V7_CSV)
    parser.add_argument("--real-weight", type=float, default=1.0)
    parser.add_argument("--synthetic-weight", type=float, default=1.0)
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing output files")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return None


def _ensure_writable(path, force):
    if path.exists() and not force:
        raise SystemExit(f"{path} exists; pass --force to overwrite")


def _site_id(row):
    """Spatial grouping unit for leakage-free evaluation.

    Prefer the woreda (the finest verified location we store), then the region,
    then the report/sample id so every real row still has a unique group.
    Synthetic rows use ``synthetic:<zone>`` in the combined file.
    """
    for key in ("woreda", "region"):
        value = (row.get(key) or "").strip()
        if value:
            return value
    if row.get("report_id") is not None:
        return f"report:{row['report_id']}"
    return f"sample:{row.get('id')}"


def _real_records(rows, args):
    records, skipped = [], []
    for row in rows:
        status = row.get("label_status")
        if status not in VALID_STATUSES:
            skipped.append((row.get("id"), "invalid label_status"))
            continue
        N = row["lab_N"] if row.get("lab_N") is not None else row["N"]
        P = row["lab_P"] if row.get("lab_P") is not None else row["P"]
        K = row["lab_K"] if row.get("lab_K") is not None else row["K"]
        ph = row["lab_ph"] if row.get("lab_ph") is not None else row["ph"]
        zone = row.get("agroecological_zone")
        soil_type = row.get("soil_type")
        if args.require_zone and not zone:
            skipped.append((row.get("id"), "missing agroecological_zone"))
            continue
        if args.require_soil_type and not soil_type:
            skipped.append((row.get("id"), "missing soil_type"))
            continue
        records.append({
            "N": float(N),
            "P": float(P),
            "K": float(K),
            "ph": float(ph),
            "zone": zone or "",
            "soil_type": soil_type or "",
            "woreda": row.get("woreda") or "",
            "site_id": _site_id(row),
            "Fertility_Status": status,
            "source": "real",
            "sample_weight": float(args.real_weight),
            "label_source": row.get("label_source") or "unknown",
        })
    return records, skipped


def _counts(records, key):
    counts = {}
    for record in records:
        counts[record[key]] = counts.get(record[key], 0) + 1
    return counts


def _write_meta(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main():
    args = parse_args()
    if args.db is not None:
        config.DB_PATH = args.db

    import pandas as pd

    import db

    db.init_db()
    rows = db.list_training_samples(
        include_in_training=True, only_verified=True, limit=100000)
    if args.label_source:
        allowed = set(args.label_source)
        rows = [row for row in rows if row.get("label_source") in allowed]

    records, skipped = _real_records(rows, args)
    if not records and not args.allow_empty:
        raise SystemExit(
            "No verified, consented training samples matched. "
            "Run tools/import_lab_results.py --apply first, or pass "
            "--allow-empty.")

    class_counts = _counts(records, "Fertility_Status")
    if args.min_per_class:
        short = {name: class_counts.get(name, 0)
                 for name in VALID_STATUSES
                 if class_counts.get(name, 0) < args.min_per_class}
        if short:
            raise SystemExit(f"not enough rows per class: {short}")

    _ensure_writable(args.out, args.force)
    real_df = pd.DataFrame.from_records(records, columns=[
        "N", "P", "K", "ph", "zone", "soil_type", "woreda", "site_id",
        "Fertility_Status", "source", "sample_weight",
    ])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    real_df.to_csv(args.out, index=False)

    meta_path = args.meta or args.out.with_suffix(".meta.json")
    meta = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "source": "db.training_samples (consented + verified)",
        "db_path": str(config.DB_PATH),
        "rows": int(len(real_df)),
        "class_counts": class_counts,
        "zone_counts": _counts(records, "zone"),
        "soil_type_counts": _counts(records, "soil_type"),
        "site_counts": _counts(records, "site_id"),
        "label_source_counts": _counts(records, "label_source"),
        "skipped": [{"id": item[0], "reason": item[1]}
                    for item in skipped],
        "dataset_sha256": _sha256(args.out),
        "columns": list(real_df.columns),
        "real_weight": args.real_weight,
    }
    _write_meta(meta_path, meta)
    print(f"wrote {len(real_df)} real samples -> {args.out}")
    print(f"metadata -> {meta_path}")

    if args.combine_with is not None:
        if not args.combine_with.exists():
            raise SystemExit(f"{args.combine_with} does not exist")
        _ensure_writable(args.combined_out, args.force)
        base = pd.read_csv(args.combine_with)
        required = {"N", "P", "K", "ph", "zone", "soil_type",
                    "Fertility_Status"}
        missing = required - set(base.columns)
        if missing:
            raise SystemExit(
                f"{args.combine_with} is missing columns: {sorted(missing)}")
        base = base.copy()
        base["source"] = "synthetic"
        base["sample_weight"] = float(args.synthetic_weight)
        # Never let a synthetic row share a grouped-CV site with real rows.
        base["site_id"] = "synthetic:" + base["zone"].astype(str)
        if "woreda" not in base.columns:
            base["woreda"] = ""
        combined = pd.concat(
            [base, real_df], ignore_index=True, sort=False)
        args.combined_out.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(args.combined_out, index=False)
        combined_meta = {
            **meta,
            "combined_from": str(args.combine_with),
            "combined_out": str(args.combined_out),
            "combined_rows": int(len(combined)),
            "synthetic_weight": args.synthetic_weight,
            "combined_sha256": _sha256(args.combined_out),
        }
        _write_meta(args.combined_out.with_suffix(".meta.json"),
                    combined_meta)
        print(f"combined {len(base)} synthetic + {len(real_df)} real "
              f"-> {args.combined_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
