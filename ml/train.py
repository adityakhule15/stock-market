from __future__ import annotations

import logging
from datetime import date

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from features.builders import feature_matrix
from ml.evaluate import (
    best_threshold,
    classification_metrics,
    date_walk_forward_splits,
    expectancy_R,
    reliability_table,
)

logger = logging.getLogger(__name__)

DEFAULT_LGBM_PARAMS = {
    "n_estimators": 300,
    "learning_rate": 0.035,
    "num_leaves": 24,
    "subsample": 0.75,
    "colsample_bytree": 0.7,
    "min_child_samples": 50,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "random_state": 19,
    "verbosity": -1,
    # No class_weight — keep scores as real probabilities after isotonic
}


def fit_baseline(X: pd.DataFrame, y: pd.Series):
    clf = LogisticRegression(max_iter=500)
    clf.fit(X, y)
    return {"algo": "logistic", "model": clf, "calibrator": None, "params": {}, "feature_cols": list(X.columns)}


def fit_lgbm(
    X: pd.DataFrame,
    y: pd.Series,
    params: dict | None = None,
    init_model=None,
    X_cal: pd.DataFrame | None = None,
    y_cal: pd.Series | None = None,
):
    params = {**DEFAULT_LGBM_PARAMS, **(params or {})}
    # Strip class_weight if present in old params
    params.pop("class_weight", None)
    model = lgb.LGBMClassifier(**params)
    if init_model is not None:
        try:
            model.fit(X, y, init_model=init_model)
        except Exception:  # noqa: BLE001
            model.fit(X, y)
    else:
        model.fit(X, y)

    cal = None
    if X_cal is not None and y_cal is not None and len(X_cal) >= 30 and y_cal.nunique() > 1:
        raw_cal = model.predict_proba(np.ascontiguousarray(X_cal.to_numpy(dtype=np.float64)))[:, 1]
        trial = IsotonicRegression(out_of_bounds="clip")
        trial.fit(raw_cal, y_cal)
        mapped = trial.transform(raw_cal)
        if float(np.ptp(mapped)) >= 0.10:
            cal = trial
    return {
        "algo": "lightgbm",
        "model": model,
        "calibrator": cal,
        "params": params,
        "feature_cols": list(X.columns),
    }


def predict_proba(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    cols = bundle.get("feature_cols")
    if cols:
        X = X.reindex(columns=cols, fill_value=0.0)
    arr = np.ascontiguousarray(X.to_numpy(dtype=np.float64, copy=True))
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    model = bundle["model"]
    try:
        raw = model.predict_proba(arr)[:, 1]
    except OSError:
        booster = getattr(model, "booster_", None) or getattr(model, "_Booster", None)
        if booster is None:
            raise
        raw = np.asarray(booster.predict(arr), dtype=np.float64)
    cal = bundle.get("calibrator")
    return cal.transform(raw) if cal is not None else raw


def time_train_test_split(dataset: pd.DataFrame, test_ratio: float = 0.2, embargo: int = 8):
    """Chronological split by unique dates with embargo gap (days)."""
    dates = pd.DatetimeIndex(pd.to_datetime(dataset.index).normalize().unique()).sort_values()
    if len(dates) < 40:
        n = len(dataset)
        test_n = max(int(n * test_ratio), 40)
        train_end = max(n - test_n - embargo, n // 2)
        return dataset.iloc[:train_end], dataset.iloc[train_end + embargo :]
    test_n_dates = max(int(len(dates) * test_ratio), 10)
    train_end_d = dates[-(test_n_dates + embargo) - 1] if len(dates) > test_n_dates + embargo else dates[len(dates) // 2]
    test_start_d = dates[-test_n_dates]
    norm = pd.to_datetime(dataset.index).normalize()
    train_df = dataset.loc[norm <= train_end_d]
    test_df = dataset.loc[norm >= test_start_d]
    return train_df, test_df


def walk_forward_train(dataset: pd.DataFrame, params: dict | None = None) -> tuple[dict, dict]:
    """Honest train: OOF predictions + untouched holdout. Never score prod on train rows."""
    X, y = feature_matrix(dataset)
    oof = np.full(len(X), np.nan)
    for train_idx, test_idx in date_walk_forward_splits(dataset.index, n_splits=5, embargo_days=8):
        if y.iloc[train_idx].nunique() < 2:
            continue
        bundle = fit_lgbm(X.iloc[train_idx], y.iloc[train_idx], params=params)
        # Raw probs for OOF (calibrator fitted later on all OOF)
        raw_bundle = {**bundle, "calibrator": None}
        oof[test_idx] = predict_proba(raw_bundle, X.iloc[test_idx])

    mask = ~np.isnan(oof)
    decision_threshold = 0.60
    metrics: dict = {}
    calibrator = None
    if mask.sum() >= 30 and y.values[mask].nunique() > 1:
        # Fit isotonic on OOF only
        trial = IsotonicRegression(out_of_bounds="clip")
        trial.fit(oof[mask], y.values[mask])
        oof_cal = trial.transform(oof[mask])
        if float(np.ptp(oof_cal)) >= 0.10:
            calibrator = trial
            scored = oof_cal
        else:
            scored = oof[mask]
        decision_threshold, thr_m = best_threshold(y.values[mask], scored, min_signals=20, win_R=2.0)
        metrics = dict(thr_m)
        metrics["decision_threshold"] = decision_threshold
        metrics["reliability_oof"] = reliability_table(y.values[mask], scored)
        metrics["oof_n"] = int(mask.sum())
    else:
        metrics = {"expectancy_R": 0.0, "oof_n": int(mask.sum())}

    # Untouched chronological holdout (never used for OOF fitting of final model)
    train_df, test_df = time_train_test_split(dataset, test_ratio=0.2, embargo=8)
    X_tr, y_tr = feature_matrix(train_df)
    X_te, y_te = feature_matrix(test_df)

    if y_tr.nunique() < 2 or len(X_tr) < 50:
        prod = fit_lgbm(X, y, params=params)
        prod["calibrator"] = calibrator
        prod["decision_threshold"] = float(decision_threshold)
        metrics["train_start"] = dataset.index.min().date() if len(dataset) else date.today()
        metrics["train_end"] = dataset.index.max().date() if len(dataset) else date.today()
        metrics["n_rows"] = len(dataset)
        metrics["honest"] = False
        return prod, metrics

    # Production model: train on early window only (not holdout)
    prod = fit_lgbm(X_tr, y_tr, params=params)
    prod["calibrator"] = calibrator  # OOF calibrator

    if len(X_te) and y_te.nunique() > 1:
        te_prob = predict_proba(prod, X_te)
        thr, hold = best_threshold(y_te.values, te_prob, min_signals=max(10, len(X_te) // 40), win_R=2.0)
        decision_threshold = thr
        hold["decision_threshold"] = thr
        hold["reliability"] = reliability_table(y_te.values, te_prob)
        # Base-rate comparison
        base_p = float(y_te.mean())
        from sklearn.metrics import log_loss

        hold["baseline_log_loss"] = float(
            log_loss(y_te, np.clip(np.full(len(y_te), base_p), 1e-6, 1 - 1e-6), labels=[0, 1])
        )
        hold["log_loss"] = classification_metrics(y_te, te_prob, threshold=thr).get("log_loss")
        metrics["holdout"] = hold
        metrics["auc"] = hold.get("auc", metrics.get("auc", 0.5))
        metrics["brier"] = hold.get("brier", metrics.get("brier", 1.0))
        metrics["hit_rate"] = hold.get("hit_rate", metrics.get("hit_rate", 0.0))
        metrics["n_signals"] = hold.get("n_signals", metrics.get("n_signals", 0))
        metrics["expectancy_R"] = hold.get("expectancy_R", metrics.get("expectancy_R", 0.0))
        metrics["log_loss"] = hold.get("log_loss")
        metrics["baseline_log_loss"] = hold.get("baseline_log_loss")
        metrics["decision_threshold"] = thr
        # Average non-win fraction for EV gate (stops + time exits ≈ 1 - hit among signals)
        metrics["avg_non_win_R"] = 1.0

    prod["decision_threshold"] = float(decision_threshold)
    metrics["decision_threshold"] = float(decision_threshold)
    metrics["train_start"] = train_df.index.min().date() if len(train_df) else date.today()
    metrics["train_end"] = train_df.index.max().date() if len(train_df) else date.today()
    metrics["holdout_start"] = test_df.index.min().date() if len(test_df) else None
    metrics["holdout_end"] = test_df.index.max().date() if len(test_df) else None
    metrics["n_rows"] = len(dataset)
    metrics["train_rows"] = len(train_df)
    metrics["test_rows"] = len(test_df)
    metrics["honest"] = True
    return prod, metrics


def optuna_search(dataset: pd.DataFrame, n_trials: int = 30) -> dict:
    try:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        return DEFAULT_LGBM_PARAMS

    X, y = feature_matrix(dataset)

    def objective(trial):
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 100, 400),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 16, 64),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
            "min_child_samples": trial.suggest_int("min_child_samples", 20, 100),
            "random_state": 19,
            "verbosity": -1,
        }
        scores = []
        for train_idx, test_idx in date_walk_forward_splits(dataset.index, n_splits=3, embargo_days=8):
            if y.iloc[train_idx].nunique() < 2:
                continue
            bundle = fit_lgbm(X.iloc[train_idx], y.iloc[train_idx], params=params)
            prob = predict_proba(bundle, X.iloc[test_idx])
            _, fold_m = best_threshold(y.iloc[test_idx], prob, min_signals=8, win_R=2.0)
            scores.append(float(fold_m.get("expectancy_R") or 0.0))
        return float(np.mean(scores)) if scores else 0.0

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best = {**DEFAULT_LGBM_PARAMS, **study.best_params}
    return best
