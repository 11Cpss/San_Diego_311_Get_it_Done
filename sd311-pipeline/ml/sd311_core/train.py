"""CatBoost MultiClass training helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool

from .features import CATEGORICAL_FEATURES, FEATURES
from .labels import BUCKETS

DEFAULT_PARAMS = {
    "loss_function": "MultiClass",
    "iterations": 500,
    "learning_rate": 0.15,
    "depth": 6,
    "l2_leaf_reg": 3.0,
    "thread_count": 2,
    "random_seed": 42,
    "early_stopping_rounds": 40,
    "verbose": 100,
    "allow_writing_files": False,  # no catboost_info/ folder
    "class_names": BUCKETS,
}


def make_pool(df: pd.DataFrame, with_label: bool = True) -> Pool:
    """Wrap the feature columns (and label) in a CatBoost Pool."""
    X = df[FEATURES].copy()
    for c in CATEGORICAL_FEATURES:
        X[c] = X[c].astype(str)  # CatBoost wants str/int categoricals
    label = df["bucket"].astype(str).to_numpy() if with_label else None
    return Pool(X, label=label, cat_features=CATEGORICAL_FEATURES)


def fit_model(train: pd.DataFrame, valid: pd.DataFrame | None = None, params: dict | None = None) -> CatBoostClassifier:
    """Fit CatBoost; if ``valid`` is given it is used for early stopping only."""
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    model = CatBoostClassifier(**p)
    eval_set = make_pool(valid) if valid is not None else None
    model.fit(make_pool(train), eval_set=eval_set, use_best_model=valid is not None)
    return model


def predict(model: CatBoostClassifier, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (predicted labels, probabilities in BUCKETS order)."""
    proba = model.predict_proba(make_pool(df, with_label=False))
    order = [list(model.classes_).index(b) for b in BUCKETS]
    proba = proba[:, order]
    return np.array(BUCKETS)[proba.argmax(axis=1)], proba


def load_model(path: str) -> CatBoostClassifier:
    """Load a model saved with ``model.save_model(path)`` (e.g. models/model.cbm)."""
    model = CatBoostClassifier()
    model.load_model(path)
    return model
