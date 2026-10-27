"""Honest evaluation gate for the Ethiopian fertility model (v7).

Trains a fresh Random Forest on a grouped split of the supplied CSV and reports
metrics on **real** rows only, next to the deterministic band-rule baseline.
A synthetic-only CSV is refused unless ``--allow-synthetic`` is passed, because
rule-derived labels can only measure rule reproduction — see
``docs/DATA_CARD.md``.

Grouping uses ``site_id`` when present (woreda/region/report id for real rows,
``synthetic:<zone>`` for generated rows), so neighbouring samples cannot leak
across the split. ``--leave-one-zone-out`` adds the transfer number: how well a
zone the model never saw is classified.

Usage:
    python3 tools/evaluate_ethio_model.py --csv Dataset/soil_data_ethio_v7.csv
    python3 tools/evaluate_ethio_model.py --csv Dataset/soil_data_ethio_v7.csv \
        --min-macro-f1 0.65 --require-beats-baseline --json-out report.json

Exit codes: 0 = all requested gates pass (or none requested), 1 = a gate
failed, 2 = nothing evaluable (no verified real rows).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                             precision_recall_fscore_support)
from sklearn.model_selection import (GroupKFold, GroupShuffleSplit,
                                     train_test_split)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import ethiopian_standards as ethio  # noqa: E402
from data_analysis import GOOD_SCORE, MODERATE_SCORE, score_nutrient  # noqa: E402
from ML.ml_model import build_features  # noqa: E402

REQUIRED_COLUMNS = {"N", "P", "K", "ph", config.TARGET}
VALID_CLASSES = ("Good", "Moderate", "Poor")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--random-state", type=int, default=40)
    parser.add_argument("--n-estimators", type=int, default=100)
    parser.add_argument(
        "--allow-synthetic", action="store_true",
        help="Evaluate a CSV without verified real rows (numbers are then "
             "rule reproduction, never accuracy claims).")
    parser.add_argument(
        "--min-macro-f1", type=float, default=None,
        help="Promotion gate: exit 1 below this real-holdout macro F1.")
    parser.add_argument(
        "--require-beats-baseline", action="store_true",
        help="Promotion gate: exit 1 unless the model beats the band rule.")
    parser.add_argument(
        "--leave-one-zone-out", action="store_true",
        help="Also report transfer accuracy with one zone held out.")
    parser.add_argument("--json-out", type=Path, default=None)
    return parser.parse_args()


def _rule_label(row):
    """Band-rule baseline label for one row (same scoring as training)."""
    zone = row.get("zone")
    bands = (ethio.zone_labeling_bands(zone)
             if isinstance(zone, str) and ethio.zone_exists(zone)
             else ethio.labeling_bands(config.FERTILITY_PROFILE))
    score = 0
    for col, col_bands in bands.items():
        score += score_nutrient(
            row[col], *col_bands["ideal"], *col_bands["near"])
    if score >= GOOD_SCORE:
        return "Good"
    if score >= MODERATE_SCORE:
        return "Moderate"
    return "Poor"


def _metrics(y_true, y_pred):
    labels = [c for c in VALID_CLASSES if c in set(y_true) | set(y_pred)]
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0)
    return {
        "rows": int(len(y_true)),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(float(f1_score(
            y_true, y_pred, labels=labels, average="macro",
            zero_division=0)), 4),
        "per_class": {
            label: {
                "precision": round(float(p), 4),
                "recall": round(float(r), 4),
                "f1": round(float(f), 4),
                "support": int(s),
            }
            for label, p, r, f, s in zip(labels, precision, recall, f1,
                                          support)
        },
        "confusion": {
            "labels": labels,
            "matrix": confusion_matrix(
                y_true, y_pred, labels=labels).tolist(),
        },
    }


def _per_zone(frame, y_pred):
    """Accuracy per zone for positional predictions (same row order)."""
    if "zone" not in frame.columns:
        return {}
    scored = frame.assign(_pred=list(y_pred))
    scores = {}
    for zone, group in scored.groupby(scored["zone"].fillna("")):
        truth = group[config.TARGET].astype(str).tolist()
        pred = [str(value) for value in group["_pred"].tolist()]
        scores[str(zone) or "unknown"] = {
            "rows": int(len(group)),
            "accuracy": round(float(accuracy_score(truth, pred)), 4),
        }
    return scores


def _split(frame, site_col, args):
    if site_col is not None and frame[site_col].nunique() >= 2:
        splitter = GroupShuffleSplit(
            n_splits=1, test_size=args.test_size,
            random_state=args.random_state)
        train_idx, test_idx = next(splitter.split(
            frame, frame[config.TARGET], frame[site_col]))
        return train_idx, test_idx, f"grouped by {site_col}"
    idx = np.arange(len(frame))
    stratify = (frame[config.TARGET]
                if frame[config.TARGET].nunique() > 1 else None)
    train_idx, test_idx = train_test_split(
        idx, test_size=args.test_size, random_state=args.random_state,
        stratify=stratify)
    return train_idx, test_idx, "random stratified (no usable groups)"


def _fit(frame, args):
    X = build_features(frame)
    y = frame[config.TARGET]
    model = RandomForestClassifier(
        n_estimators=args.n_estimators, random_state=args.random_state)
    weights = None
    if "sample_weight" in frame.columns:
        weights = frame["sample_weight"].astype(float).to_numpy()
    return X, y, model, weights


def main():
    args = parse_args()
    df = pd.read_csv(args.csv)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise SystemExit(f"{args.csv} is missing columns: {sorted(missing)}")
    if df[config.TARGET].isna().any():
        raise SystemExit("Fertility_Status contains empty labels")

    if "source" in df.columns:
        real = df[df["source"].astype(str) == "real"].copy()
        provenance = "real" if len(real) else "synthetic"
    else:
        real = df.iloc[0:0].copy()
        provenance = "unlabeled"

    if real.empty:
        if not args.allow_synthetic:
            print("No verified real rows in this CSV.")
            print("Real labels come from tools/import_lab_results.py --apply;")
            print("a rule-derived set cannot support an accuracy claim.")
            print("Re-run with --allow-synthetic to measure rule reproduction.")
            return 2
        frame = df.copy()
        provenance = "synthetic"
        print("WARNING: evaluating synthetic/rule-derived labels only — these "
              "numbers are rule reproduction, not field accuracy.")
    else:
        frame = real

    frame = frame.reset_index(drop=True)
    site_col = "site_id" if "site_id" in frame.columns else (
        "zone" if "zone" in frame.columns else None)
    train_idx, test_idx, split_note = _split(frame, site_col, args)
    train, test = frame.iloc[train_idx], frame.iloc[test_idx]
    if train.empty or test.empty:
        raise SystemExit("split produced an empty train or test fold")

    X_train, y_train, model, train_weights = _fit(train, args)
    fit_kwargs = {}
    if train_weights is not None:
        fit_kwargs["sample_weight"] = train_weights
    model.fit(X_train, y_train, **fit_kwargs)

    X_test = build_features(test)
    y_true = test[config.TARGET].astype(str).tolist()
    y_pred = [str(v) for v in model.predict(X_test)]
    rule_pred = [_rule_label(row) for _, row in test.iterrows()]

    model_metrics = _metrics(y_true, y_pred)
    rule_metrics = _metrics(y_true, rule_pred)

    report = {
        "csv": str(args.csv),
        "provenance": provenance,
        "rows_evaluated": int(len(frame)),
        "split": split_note,
        "test_rows": int(len(test)),
        "model": model_metrics,
        "rule_baseline": rule_metrics,
        "per_zone": _per_zone(test, y_pred),
    }

    if args.leave_one_zone_out and "zone" in frame.columns \
            and frame["zone"].nunique() >= 2:
        X_all, y_all, model_all, weights_all = _fit(frame, args)
        groups = frame["zone"].astype(str)
        folds = []
        for train_i, test_i in GroupKFold(
                n_splits=min(4, groups.nunique())).split(
                    X_all, y_all, groups):
            fold_model = RandomForestClassifier(
                n_estimators=args.n_estimators,
                random_state=args.random_state)
            fold_kwargs = {}
            if weights_all is not None:
                fold_kwargs["sample_weight"] = weights_all[train_i]
            fold_model.fit(X_all.iloc[train_i], y_all.iloc[train_i],
                           **fold_kwargs)
            folds.append(round(float(fold_model.score(
                X_all.iloc[test_i], y_all.iloc[test_i])), 4))
        report["leave_one_zone_out_accuracy"] = folds

    print(f"csv:        {args.csv}")
    print(f"provenance: {provenance}")
    print(f"split:      {split_note}, {len(test)} test rows")
    print(f"model:      accuracy={model_metrics['accuracy']} "
          f"macro_f1={model_metrics['macro_f1']}")
    print(f"rule:       accuracy={rule_metrics['accuracy']} "
          f"macro_f1={rule_metrics['macro_f1']}")
    for label, values in model_metrics["per_class"].items():
        print(f"  {label:<9} precision={values['precision']} "
              f"recall={values['recall']} support={values['support']}")
    if report["per_zone"]:
        print("per zone:   " + ", ".join(
            f"{zone}={values['accuracy']}"
            for zone, values in report["per_zone"].items()))
    if "leave_one_zone_out_accuracy" in report:
        print(f"leave-one-zone-out: {report['leave_one_zone_out_accuracy']}")

    failed = []
    if args.min_macro_f1 is not None \
            and model_metrics["macro_f1"] < args.min_macro_f1:
        failed.append(
            f"macro_f1 {model_metrics['macro_f1']} < {args.min_macro_f1}")
    if args.require_beats_baseline \
            and model_metrics["accuracy"] <= rule_metrics["accuracy"]:
        failed.append(
            "model accuracy does not beat the band rule "
            f"({model_metrics['accuracy']} <= {rule_metrics['accuracy']})")

    report["gates"] = {
        "min_macro_f1": args.min_macro_f1,
        "require_beats_baseline": args.require_beats_baseline,
        "failed": failed,
        "passed": not failed,
    }

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n",
                                 encoding="utf-8")
        print(f"json:       {args.json_out}")

    if failed:
        print("GATE FAILED: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
