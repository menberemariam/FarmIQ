import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

df = pd.read_csv(config.CROP_DATA_CSV)

X = df[config.CROP_FEATURES]
y = df["label"]

X_train, X_test, y_train, y_test = train_test_split(
    X, y, random_state=40, test_size=0.1, stratify=y
)

rf = RandomForestClassifier(n_estimators=200, random_state=40)
rf.fit(X_train, y_train)

cv = StratifiedKFold(5, shuffle=True, random_state=40)
cv_scores = cross_val_score(rf, X, y, cv=cv)
print(f"5-fold CV accuracy: {cv_scores.mean():.3f} (+/- {cv_scores.std():.3f})")

joblib.dump(rf, config.CROP_MODEL_PKL)
print(f"Model saved to {config.CROP_MODEL_PKL}")
print(f"Crops: {len(rf.classes_)}")

y_pred = rf.predict(X_test)
print(f"Test accuracy: {accuracy_score(y_test, y_pred):.3f}")
print(classification_report(y_test, y_pred, zero_division=0))
