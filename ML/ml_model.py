import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, f1_score, recall_score, precision_score
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score

import mlflow
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

df = pd.read_csv(config.SOIL_DATA_CSV)

X = df[config.FEATURE_COLS]
y = df[config.TARGET]

X_train, X_test, y_train, y_test = train_test_split(
    X, y, random_state=40, test_size=0.1, stratify=y
)

rf = RandomForestClassifier(n_estimators=100, random_state=40)
rf.fit(X_train, y_train)

y_pred = rf.predict(X_test)

cv = StratifiedKFold(5, shuffle=True, random_state=40)
cv_scores = cross_val_score(rf, X, y, cv=cv)
print(f"5-fold CV accuracy: {cv_scores.mean():.3f} (+/- {cv_scores.std():.3f})")

joblib.dump(rf, config.SOIL_MODEL_PKL)
print(f"Model saved to {config.SOIL_MODEL_PKL}")

print(classification_report(y_test, y_pred))

metrics = {
    "precision": precision_score(y_test, y_pred, average="macro"),
    "recall": recall_score(y_test, y_pred, average="macro"),
    "f1": f1_score(y_test, y_pred, average="macro"),
    "cv_accuracy": cv_scores.mean(),
}

mlflow.set_experiment("Soil Analysis")
with mlflow.start_run():
    mlflow.set_tag("model", "random-forest-v2-corrected-labels")
    mlflow.sklearn.log_model(rf, "model")
    mlflow.log_metrics(metrics)
