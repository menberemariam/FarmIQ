"""Train the soil-fertility Random Forest (labels come from data_analysis.py).

Defaults (legacy path):  Dataset/soil_data.csv -> ML/soil_analysis.pkl
Agroecology retrain (zone-synthetic data):

    SOIL_TRAIN_DATA=Dataset/soil_data_ethio.csv \
    SOIL_MODEL_OUT=ML/soil_analysis_ethio.pkl \
    SOIL_MODEL_TAG=random-forest-v5-agroecology-zones \
    python3 ML/ml_model.py

MLflow logs the model tag, the fertility profile, the training-data path, a
dataset sha256, the hyperparameters and the metrics — so every artifact can be
reproduced from its run (F22: params + dataset hash).
"""

import hashlib
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, classification_report, f1_score,
                             precision_score, recall_score)
from sklearn.model_selection import (GroupKFold, GroupShuffleSplit,
                                     StratifiedKFold, train_test_split)

import mlflow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
import ethiopian_standards as ethio

N_ESTIMATORS = 100
RANDOM_STATE = 40
TEST_SIZE = 0.1


def build_features(df):
    """N/P/K/ph, plus one-hot zone and soil_type columns when present.

    The zone-synthetic dataset labels each row with ITS zone's bands, so
    the same N value is 'ideal' in one zone and 'deficient' in another — the
    model must see the zone to learn the Ethiopian rules. The v6 dataset
    additionally carries a within-zone soil_type column (LRF schema); when
    present it is one-hot encoded after the zone columns with the same
    zero-padding discipline. Trained models carry the resulting column list
    in ``feature_cols_``.
    """
    X = df[config.FEATURE_COLS].copy()
    if "zone" in df.columns:
        dummies = pd.get_dummies(df["zone"], prefix="zone").astype(int)
        for zone in ethio.list_zones():
            col = f"zone_{zone}"
            if col not in dummies.columns:
                dummies[col] = 0
        X = pd.concat(
            [X, dummies[[f"zone_{z}" for z in ethio.list_zones()]]], axis=1)
    if "soil_type" in df.columns:
        dummies = pd.get_dummies(df["soil_type"], prefix="soil_type").astype(int)
        for soil_id in ethio.list_soil_types():
            col = f"soil_type_{soil_id}"
            if col not in dummies.columns:
                dummies[col] = 0
        X = pd.concat(
            [X, dummies[[f"soil_type_{s}" for s in ethio.list_soil_types()]]],
            axis=1)
    return X


def dataset_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_tag(model_out):
    name = str(model_out)
    if name.endswith("ethio_v7.pkl"):
        return "random-forest-v7-real-ethio"
    if name.endswith("ethio_v6.pkl"):
        return "random-forest-v6-soiltype-lrf"
    if name.endswith("ethio.pkl"):
        return "random-forest-v5-agroecology-zones"
    return "random-forest-v4-ethiosis-p2.6"


def _optional_column(df, env_name):
    """Return (column_name, values) for an opt-in training helper column."""
    column = os.getenv(env_name, "").strip()
    if not column or column not in df.columns:
        return None, None
    return column, df[column]


def _cross_validate(rf, X, y, splits, weights):
    """Accuracy per precomputed split, honouring optional sample weights.

    cross_val_score cannot forward sample_weight portably, so real-data
    training uses this small explicit loop. The default path produces the
    same StratifiedKFold splits as before.
    """
    scores = []
    for train_idx, test_idx in splits:
        model = clone(rf)
        fit_kwargs = {}
        if weights is not None:
            fit_kwargs["sample_weight"] = weights[train_idx]
        model.fit(X.iloc[train_idx], y.iloc[train_idx], **fit_kwargs)
        scores.append(accuracy_score(
            y.iloc[test_idx], model.predict(X.iloc[test_idx])))
    return scores


def main():
    data_path = Path(os.getenv("SOIL_TRAIN_DATA", str(config.SOIL_DATA_CSV)))
    model_out = Path(os.getenv("SOIL_MODEL_OUT", str(config.SOIL_MODEL_PKL)))
    data_hash = dataset_sha256(data_path)

    df = pd.read_csv(data_path)
    X = build_features(df)
    y = df[config.TARGET]

    group_col, group_values = _optional_column(df, "SOIL_GROUP_COL")
    weight_col, weight_values = _optional_column(
        df, "SOIL_SAMPLE_WEIGHT_COL")
    weights = (weight_values.astype(float).to_numpy()
               if weight_values is not None else None)

    indices = np.arange(len(df))
    groups = group_values.astype(str) if group_col is not None else None
    n_groups = groups.nunique() if groups is not None else 0
    if groups is not None and n_groups >= 2:
        # A group split with fewer groups than folds would otherwise raise.
        splitter = GroupShuffleSplit(
            n_splits=1, test_size=TEST_SIZE, random_state=RANDOM_STATE)
        train_idx, test_idx = next(splitter.split(X, y, groups))
        n_folds = min(5, n_groups)
        cv_splits = list(GroupKFold(n_splits=n_folds).split(X, y, groups))
    else:
        if groups is not None:
            print(f"SOIL_GROUP_COL={group_col!r} has fewer than 2 groups; "
                  "falling back to a stratified split")
            groups = None
        train_idx, test_idx = train_test_split(
            indices, random_state=RANDOM_STATE, test_size=TEST_SIZE,
            stratify=y)
        n_folds = 5
        cv_splits = list(StratifiedKFold(
            n_folds, shuffle=True, random_state=RANDOM_STATE).split(X, y))

    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

    rf = RandomForestClassifier(n_estimators=N_ESTIMATORS, random_state=RANDOM_STATE)
    fit_kwargs = {}
    if weights is not None:
        fit_kwargs["sample_weight"] = weights[train_idx]
    rf.fit(X_train, y_train, **fit_kwargs)
    # Persist the exact input schema so serving code can rebuild the row.
    rf.feature_cols_ = list(X.columns)

    y_pred = rf.predict(X_test)

    cv_scores = _cross_validate(rf, X, y, cv_splits, weights)
    cv_mean = sum(cv_scores) / len(cv_scores)
    cv_std = (sum((score - cv_mean) ** 2 for score in cv_scores)
              / len(cv_scores)) ** 0.5
    print(f"{n_folds}-fold CV accuracy: {cv_mean:.3f} (+/- {cv_std:.3f})")

    model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(rf, model_out)
    print(f"Model saved to {model_out}")

    print(classification_report(y_test, y_pred))

    metrics = {
        "precision": precision_score(y_test, y_pred, average="macro"),
        "recall": recall_score(y_test, y_pred, average="macro"),
        "f1": f1_score(y_test, y_pred, average="macro"),
        "cv_accuracy": cv_mean,
    }

    tag = os.getenv("SOIL_MODEL_TAG") or default_tag(model_out)
    mlflow.set_experiment("Soil Analysis")
    with mlflow.start_run():
        mlflow.set_tag("model", tag)
        mlflow.set_tag("fertility_profile", config.FERTILITY_PROFILE)
        mlflow.log_param("train_data", str(data_path))
        mlflow.log_param("dataset_sha256", data_hash)
        mlflow.log_param("n_estimators", N_ESTIMATORS)
        mlflow.log_param("random_state", RANDOM_STATE)
        mlflow.log_param("test_size", TEST_SIZE)
        mlflow.log_param("feature_cols", ",".join(X.columns))
        mlflow.log_param("label_classes", ",".join(sorted(y.unique())))
        mlflow.log_param("group_col", group_col or "")
        mlflow.log_param("sample_weight_col", weight_col or "")
        if "source" in df.columns:
            mlflow.log_param(
                "real_rows", int((df["source"] == "real").sum()))
            mlflow.log_param(
                "synthetic_rows", int((df["source"] == "synthetic").sum()))
        mlflow.sklearn.log_model(rf, "model")
        mlflow.log_metrics(metrics)


if __name__ == "__main__":
    main()
