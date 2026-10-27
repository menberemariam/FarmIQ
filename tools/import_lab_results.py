"""Attach verified lab measurements/labels to consented training samples.

The capture endpoint stores unlabeled samples only. This tool is the gate that
turns a sample into training data: it validates the lab values, canonicalizes
the Ethiopian context, derives or accepts the Good/Moderate/Poor label, and
records who verified it.

Input CSV: one row per sample. Identify the sample with ``report_id`` or
``sample_hash`` and provide lab N/P/K/pH (kg/ha by default, or set
``input_units=lab`` for % N, mg/kg P, cmolc/kg K). Optional columns:
lab_status, label_source, label_confidence, verified_by, agroecological_zone,
soil_type, crop, variety, region, season, texture_class, soc_pct, soc_class,
woreda, dataset_version.

Safety gates:

* content dedupe — the same N/P/K/pH + zone + soil_type already verified on
  another sample is rejected (``--allow-duplicates`` overrides);
* coverage report every run; ``--require-coverage`` turns the class/zone
  minimums into a non-zero exit (off by default: incremental imports start
  thin);
* ``--infer-zone-from-woreda`` fills a missing zone from the optional
  ``Dataset/woreda_soil_defaults.json`` lookup only (unknown stays unset).

Examples:
    python3 tools/import_lab_results.py --csv lab_results.csv
    python3 tools/import_lab_results.py --csv lab_results.csv --apply
    python3 tools/import_lab_results.py --csv lab_results.csv --apply \
        --db /path/to/soil_reports.db
    python3 tools/import_lab_results.py --csv lab_results.csv --apply \
        --require-coverage
"""

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import ethiopian_standards as ethio  # noqa: E402
from data_analysis import (  # noqa: E402
    GOOD_SCORE,
    MODERATE_SCORE,
    score_nutrient,
)

VALID_STATUSES = ("Good", "Moderate", "Poor")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=None,
                        help="SQLite DB path (default: config.DB_PATH)")
    parser.add_argument("--apply", action="store_true",
                        help="Write the verified labels (default: dry run)")
    parser.add_argument("--default-verified-by", default="lab-import")
    parser.add_argument("--dataset-version", default="real-v1")
    parser.add_argument(
        "--allow-duplicates", action="store_true",
        help="Accept a row whose N/P/K/pH + zone + soil_type already exists "
             "with a verified label on another sample (default: reject, so a "
             "re-sent lab sheet cannot inflate a class).")
    parser.add_argument(
        "--infer-zone-from-woreda", action="store_true",
        help="Use Dataset/woreda_soil_defaults.json to fill a missing zone "
             "from the woreda. Unknown woredas stay unset (never guessed).")
    parser.add_argument(
        "--woreda-defaults", type=Path, default=None,
        help="Woreda lookup file (default: Dataset/woreda_soil_defaults.json).")
    parser.add_argument(
        "--require-coverage", action="store_true",
        help="Exit non-zero when the verified dataset is below the class/zone "
             "minimums. Off by default because incremental imports start thin.")
    parser.add_argument(
        "--min-per-class", type=int, default=config.TRAINING_MIN_ROWS_PER_CLASS)
    parser.add_argument(
        "--min-per-zone", type=int, default=config.TRAINING_MIN_ROWS_PER_ZONE)
    return parser.parse_args()


def _text(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _number(row, *names):
    raw = _text(row, *names)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{names[0]} must be numeric, got {raw!r}")


def _context(row, sample, *columns):
    return _text(row, *columns) or sample.get(columns[0])


def _canonical_zone(value):
    if not value:
        return None
    zone = ethio.canonical_zone(value)
    if zone is None:
        raise ValueError(f"unknown agroecological zone {value!r}")
    return zone


def load_woreda_zones(path):
    """Woreda -> zone map from the optional lookup JSON (empty by default).

    Accepts either ``{"woreda": "zone_id"}`` or an entry dict with an
    ``agroecological_zone``/``zone`` key. Unknown woredas are simply absent;
    nothing is guessed.
    """
    if path is None or not Path(path).exists():
        return {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    table = {}
    for name, entry in (raw.get("woredas") or {}).items():
        zone = entry.get("agroecological_zone") if isinstance(entry, dict) else entry
        if isinstance(zone, dict):
            zone = zone.get("agroecological_zone") or zone.get("zone")
        canonical = ethio.canonical_zone(zone) if isinstance(zone, str) else None
        if canonical:
            table[str(name).strip().lower()] = canonical
    return table


def _woreda_zone(woreda, table):
    if not woreda or not table:
        return None
    return table.get(str(woreda).strip().lower())


def _canonical_soil_type(value):
    if not value:
        return None
    soil_type = ethio.canonical_soil_type(value)
    if soil_type is None:
        raise ValueError(f"unknown soil type {value!r}")
    return soil_type


def _convert_units(N, P, K, units):
    normalized = (units or "kg_ha").strip().lower()
    if normalized in ("kg_ha", "kg/ha", "kgha", "kg-ha"):
        return N, P, K
    if normalized in ("lab", "lab_units", "lab-units"):
        return (
            ethio.n_pct_to_kg_ha(N),
            ethio.p_mgkg_to_kg_ha(P),
            ethio.k_cmolc_to_kg_ha(K),
        )
    raise ValueError(f"input_units must be kg_ha or lab, got {units!r}")


def _measurements(row, sample):
    lab_N = _number(row, "lab_N", "N", "n")
    lab_P = _number(row, "lab_P", "P", "p")
    lab_K = _number(row, "lab_K", "K", "k")
    lab_ph = _number(row, "lab_ph", "ph", "pH")
    if all(value is None for value in (lab_N, lab_P, lab_K, lab_ph)):
        lab_N = sample.get("lab_N")
        lab_P = sample.get("lab_P")
        lab_K = sample.get("lab_K")
        lab_ph = sample.get("lab_ph")
    if any(value is None for value in (lab_N, lab_P, lab_K, lab_ph)):
        raise ValueError("lab_N, lab_P, lab_K and lab_ph are all required")
    units = _text(row, "input_units") or sample.get("input_units") or "kg_ha"
    N, P, K = _convert_units(float(lab_N), float(lab_P), float(lab_K), units)
    return N, P, K, float(lab_ph)


def _derive_status(N, P, K, ph, zone):
    bands = (ethio.zone_labeling_bands(zone) if zone
             else ethio.labeling_bands(config.FERTILITY_PROFILE))
    values = {"N": N, "P": P, "K": K, "ph": ph}
    score = 0
    for col, col_bands in bands.items():
        score += score_nutrient(
            values[col], *col_bands["ideal"], *col_bands["near"])
    if score >= GOOD_SCORE:
        return "Good"
    if score >= MODERATE_SCORE:
        return "Moderate"
    return "Poor"


def _validate_range(name, value, lo, hi):
    if not (lo <= value <= hi):
        raise ValueError(f"{name}={value} outside plausible range {lo}-{hi}")


def _row_context(row, sample, woreda_zones=None, infer_zone=False):
    context = {
        "agroecological_zone": _canonical_zone(
            _context(row, sample, "agroecological_zone", "zone")),
        "soil_type": _canonical_soil_type(
            _context(row, sample, "soil_type")),
        "crop": _context(row, sample, "crop"),
        "variety": _context(row, sample, "variety"),
        "region": _context(row, sample, "region"),
        "season": _context(row, sample, "season"),
        "texture_class": _context(row, sample, "texture_class"),
        "soc_pct": _number(row, "soc_pct") if _text(row, "soc_pct") else
        sample.get("soc_pct"),
        "soc_class": _context(row, sample, "soc_class"),
        "woreda": _context(row, sample, "woreda"),
    }
    if (infer_zone and context["agroecological_zone"] is None
            and context["woreda"]):
        context["agroecological_zone"] = _woreda_zone(
            context["woreda"], woreda_zones or {})
    return context


def _content_key(N, P, K, ph, zone, soil_type):
    """Identity of a measurement for duplicate detection.

    Deliberately coarser than ``db.training_sample_hash`` (which also hashes
    crop/variety/season): the same soil values re-entered under a different
    crop are still the same physical sample and must not count twice.
    """
    return (round(float(N), 2), round(float(P), 2), round(float(K), 2),
            round(float(ph), 2), zone or "", soil_type or "")


def _verified_content_index(db):
    """Content key -> set of already-verified sample ids."""
    index = {}
    for sample in db.list_training_samples(
            include_in_training=True, only_verified=True, limit=100000):
        N = sample.get("lab_N") if sample.get("lab_N") is not None else sample["N"]
        P = sample.get("lab_P") if sample.get("lab_P") is not None else sample["P"]
        K = sample.get("lab_K") if sample.get("lab_K") is not None else sample["K"]
        ph = sample.get("lab_ph") if sample.get("lab_ph") is not None else sample["ph"]
        key = _content_key(N, P, K, ph, sample.get("agroecological_zone"),
                           sample.get("soil_type"))
        index.setdefault(key, set()).add(sample["id"])
    return index


def _coverage(rows):
    """Class/zone/soil-type counts for the coverage report and gate."""
    classes, zones, soils = {}, {}, {}
    for row in rows:
        status = row.get("label_status")
        classes[status] = classes.get(status, 0) + 1
        zone = row.get("agroecological_zone") or "unknown"
        zones[zone] = zones.get(zone, 0) + 1
        soil = row.get("soil_type") or "unknown"
        soils[soil] = soils.get(soil, 0) + 1
    return {"classes": classes, "zones": zones, "soil_types": soils}


def prepare_row(row, db, defaults):
    sample_hash = _text(row, "sample_hash")
    report_id = _number(row, "report_id")
    if report_id is not None:
        report_id = int(report_id)
    if sample_hash is None and report_id is None:
        raise ValueError("one of sample_hash or report_id is required")
    sample = db.find_training_sample(
        report_id=report_id, sample_hash=sample_hash)
    if sample is None:
        raise ValueError("no matching training sample")
    if not sample.get("consent"):
        raise ValueError("sample has no training consent")

    N, P, K, ph = _measurements(row, sample)
    _validate_range("N", N, 0, 400)
    _validate_range("P", P, 0, 150)
    _validate_range("K", K, 0, 1500)
    _validate_range("ph", ph, 3.5, 9.5)

    context = _row_context(
        row, sample, woreda_zones=defaults.get("woreda_zones"),
        infer_zone=defaults.get("infer_zone_from_woreda", False))
    status = _text(row, "lab_status", "status")
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(
                f"lab_status must be one of {VALID_STATUSES}, got {status!r}")
        label_source = _text(row, "label_source") or "lab_measured"
    else:
        status = _derive_status(
            N, P, K, ph, context["agroecological_zone"])
        label_source = _text(row, "label_source") or "lab_rule_derived"

    return {
        "sample": sample,
        "N": N, "P": P, "K": K, "ph": ph,
        "status": status,
        "label_source": label_source,
        "label_confidence": _number(row, "label_confidence"),
        "verified_by": (_text(row, "verified_by")
                        or _text(row, "reviewer")
                        or defaults["verified_by"]),
        "dataset_version": (_text(row, "dataset_version")
                            or defaults["dataset_version"]),
        "context": context,
    }


def apply_row(prepared, db):
    context = prepared["context"]
    sample = prepared["sample"]
    # Fill missing context before verification so the stored row is complete.
    db.update_training_sample_context(
        sample["id"], agroecological_zone=context["agroecological_zone"],
        soil_type=context["soil_type"], crop=context["crop"],
        variety=context["variety"], region=context["region"],
        season=context["season"], texture_class=context["texture_class"],
        soc_pct=context["soc_pct"], soc_class=context["soc_class"],
        woreda=context["woreda"])
    return db.verify_training_sample(
        sample["id"], prepared["status"], prepared["label_source"],
        label_confidence=prepared["label_confidence"],
        verified_by=prepared["verified_by"], include_in_training=True,
        dataset_version=prepared["dataset_version"],
        lab_N=prepared["N"], lab_P=prepared["P"],
        lab_K=prepared["K"], lab_ph=prepared["ph"],
    )


def main():
    args = parse_args()
    if args.db is not None:
        config.DB_PATH = args.db

    import db

    db.init_db()
    woreda_defaults = args.woreda_defaults or (
        config.DATASET_DIR / "woreda_soil_defaults.json")
    defaults = {
        "verified_by": args.default_verified_by,
        "dataset_version": args.dataset_version,
        "woreda_zones": load_woreda_zones(woreda_defaults),
        "infer_zone_from_woreda": args.infer_zone_from_woreda,
    }
    verified_index = _verified_content_index(db)
    accepted, rejected, accepted_rows = 0, [], []
    starting_rows = db.list_training_samples(
        include_in_training=True, only_verified=True, limit=100000)
    with args.csv.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for line_no, row in enumerate(reader, start=2):
            try:
                prepared = prepare_row(row, db, defaults)
                key = _content_key(
                    prepared["N"], prepared["P"], prepared["K"],
                    prepared["ph"],
                    prepared["context"]["agroecological_zone"],
                    prepared["context"]["soil_type"])
                other_ids = verified_index.get(key, set()) - {
                    prepared["sample"]["id"]}
                if other_ids and not args.allow_duplicates:
                    raise ValueError(
                        "duplicate measurement: the same N/P/K/pH + zone + "
                        "soil_type is already verified on sample(s) "
                        f"{sorted(other_ids)}; pass --allow-duplicates to "
                        "keep it")
                if args.apply:
                    updated = apply_row(prepared, db)
                    if updated is None:
                        raise ValueError("sample id not found during update")
                verified_index.setdefault(key, set()).add(
                    prepared["sample"]["id"])
                accepted_rows.append(prepared)
                accepted += 1
                print(f"line {line_no}: sample {prepared['sample']['id']} "
                      f"-> {prepared['status']}")
            except Exception as exc:
                rejected.append((line_no, str(exc)))
                print(f"line {line_no}: REJECTED: {exc}")

    mode = "applied" if args.apply else "dry run"
    print(f"{mode}: {accepted} accepted, {len(rejected)} rejected")

    # Coverage is projected in a dry run and read back from the DB after apply.
    if args.apply:
        coverage_rows = db.list_training_samples(
            include_in_training=True, only_verified=True, limit=100000)
    else:
        coverage_rows = [
            {
                "label_status": sample.get("label_status"),
                "agroecological_zone": sample.get("agroecological_zone"),
                "soil_type": sample.get("soil_type"),
            }
            for sample in starting_rows
        ] + [
            {
                "label_status": prepared["status"],
                "agroecological_zone":
                    prepared["context"]["agroecological_zone"],
                "soil_type": prepared["context"]["soil_type"],
            }
            for prepared in accepted_rows
        ]
    coverage = _coverage(coverage_rows)
    print(f"coverage ({'db' if args.apply else 'projected'}): "
          f"classes={coverage['classes']} zones={coverage['zones']} "
          f"soil_types={coverage['soil_types']}")

    if not args.apply and accepted:
        print("re-run with --apply to write these labels")

    if args.require_coverage:
        short_classes = {
            name: coverage["classes"].get(name, 0)
            for name in VALID_STATUSES
            if coverage["classes"].get(name, 0) < args.min_per_class}
        short_zones = {
            zone: count for zone, count in coverage["zones"].items()
            if count < args.min_per_zone}
        if short_classes or short_zones:
            print(f"coverage below minimum: classes={short_classes} "
                  f"zones={short_zones}")
            print(f"need >= {args.min_per_class} per class and "
                  f">= {args.min_per_zone} per zone")
            return 1

    return 0 if not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
