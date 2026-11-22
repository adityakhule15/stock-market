from __future__ import annotations

import logging
from datetime import date

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from features.builders import feature_matrix
from ml.evaluate import best_threshold, classification_metrics, expectancy_R, purged_walk_forward_splits

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
    "class_weight": "balanced",
}


def fit_baseline(X: pd.DataFrame, y: pd.Series):
    clf = LogisticRegression(max_iter=500, class_weight="balanced")
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
    model = lgb.LGBMClassifier(**params)
    if init_model is not None:
        try:
            model.fit(X, y, init_model=init_model)
        except Exception:  # noqa: BLE001
            model.fit(X, y)
    else:
        model.fit(X, y)

    # Optional isotonic calibration on holdout — discard if it collapses scores
    cal = None
    if X_cal is not None and y_cal is not None and len(X_cal) >= 30 and y_cal.nunique() > 1:
        raw_cal = model.predict_proba(X_cal)[:, 1]
        trial = IsotonicRegression(out_of_bounds="clip")
        trial.fit(raw_cal, y_cal)
        mapped = trial.transform(raw_cal)
        if float(np.ptp(mapped)) >= 0.15 and float(np.percentile(mapped, 90)) >= 0.35:
            cal = trial
    else:
        n = len(X)
        cut = max(int(n * 0.8), 50)
        if n - cut >= 20 and y.iloc[cut:].nunique() > 1:
            raw_cal = model.predict_proba(X.iloc[cut:])[:, 1]
            trial = IsotonicRegression(out_of_bounds="clip")
            trial.fit(raw_cal, y.iloc[cut:])
            mapped = trial.transform(raw_cal)
            if float(np.ptp(mapped)) >= 0.15 and float(np.percentile(mapped, 90)) >= 0.35:
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
    # Contiguous float64 avoids LightGBM native crashes on some Windows/numpy builds
    arr = np.ascontiguousarray(X.to_numpy(dtype=np.float64, copy=True))
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    model = bundle["model"]
    try:
        raw = model.predict_proba(arr)[:, 1]
    except OSError:
        # Fallback: Booster.predict if sklearn wrapper handle is stale after DLL upgrade
        booster = getattr(model, "booster_", None) or getattr(model, "_Booster", None)
        if booster is None:
            raise
        raw = np.asarray(booster.predict(arr), dtype=np.float64)
    cal = bundle.get("calibrator")
    return cal.transform(raw) if cal is not None else raw


def time_train_test_split(dataset: pd.DataFrame, test_ratio: float = 0.2, embargo: int = 5):
    """Chronological split (no shuffle) with embargo gap to reduce label leakage."""
    n = len(dataset)
    test_n = max(int(n * test_ratio), 40)
    train_end = n - test_n - embargo
    if train_end < 80:
        train_end = max(n // 2, 50)
        test_start = train_end + embargo
    else:
        test_start = train_end + embargo
    train_df = dataset.iloc[:train_end]
    test_df = dataset.iloc[test_start:]
    return train_df, test_df


def walk_forward_train(dataset: pd.DataFrame, params: dict | None = None) -> tuple[dict, dict]:
    X, y = feature_matrix(dataset)
    oof = np.full(len(X), np.nan)
    for train_idx, test_idx in purged_walk_forward_splits(dataset.index, n_splits=5, embargo=5):
        if y.iloc[train_idx].nunique() < 2:
            continue
        bundle = fit_lgbm(X.iloc[train_idx], y.iloc[train_idx], params=params)
        oof[test_idx] = predict_proba(bundle, X.iloc[test_idx])
    mask = ~np.isnan(oof)
    decision_threshold = 0.60
    if mask.any():
        decision_threshold, thr_m = best_threshold(y.values[mask], oof[mask], min_signals=20)
        metrics = dict(thr_m)
        metrics["decision_threshold"] = decision_threshold
    else:
        metrics = {}
        metrics["expectancy_R"] = 0.0
    metrics["max_dd"] = 0.0

    # Final model: train on early window, calibrate on later window
    train_df, test_df = time_train_test_split(dataset, test_ratio=0.2, embargo=5)
    X_tr, y_tr = feature_matrix(train_df)
    X_te, y_te = feature_matrix(test_df)
    if y_tr.nunique() < 2:
        final = fit_lgbm(X, y, params=params)
    else:
        final = fit_lgbm(X_tr, y_tr, params=params, X_cal=X_te, y_cal=y_te)
        # Holdout metrics (honest test set) at optimized threshold
        if len(X_te) and y_te.nunique() > 1:
            te_prob = predict_proba(final, X_te)
            thr, hold = best_threshold(y_te.values, te_prob, min_signals=max(10, len(X_te) // 40))
            decision_threshold = thr
            hold["decision_threshold"] = thr
            metrics["holdout"] = hold
            # Prefer holdout for promotion stats when available
            metrics["auc"] = hold.get("auc", metrics.get("auc", 0.5))
            metrics["brier"] = hold.get("brier", metrics.get("brier", 1.0))
            metrics["hit_rate"] = hold.get("hit_rate", metrics.get("hit_rate", 0.0))
            metrics["n_signals"] = hold.get("n_signals", metrics.get("n_signals", 0))
            metrics["expectancy_R"] = hold.get("expectancy_R", metrics.get("expectancy_R", 0.0))
            metrics["decision_threshold"] = thr

    # Refit production model on all data except last embargo for mild freshness.
    # Re-score threshold on holdout with the production model; keep calibrator only
    # if it does not destroy selective positive expectancy.
    X_all, y_all = feature_matrix(dataset)
    prod = fit_lgbm(X_all.iloc[:-5] if len(X_all) > 60 else X_all, y_all.iloc[:-5] if len(y_all) > 60 else y_all, params=params)
    raw_prod = predict_proba({**prod, "calibrator": None}, X_te) if len(X_te) else None
    thr_raw, hold_raw = (
        best_threshold(y_te.values, raw_prod, min_signals=max(10, len(X_te) // 50))
        if raw_prod is not None and len(X_te) and y_te.nunique() > 1
        else (decision_threshold, {})
    )
    keep_cal = False
    if final.get("calibrator") is not None and raw_prod is not None and len(X_te):
        prod_cal = dict(prod)
        prod_cal["calibrator"] = final["calibrator"]
        cal_prob = predict_proba(prod_cal, X_te)
        thr_cal, hold_cal = best_threshold(y_te.values, cal_prob, min_signals=max(10, len(X_te) // 50))
        # Prefer calibrator only if expectancy is better (or equal with higher hit-rate)
        if float(hold_cal.get("expectancy_R") or -999) > float(hold_raw.get("expectancy_R") or -999) + 0.02:
            keep_cal = True
            decision_threshold = thr_cal
            metrics["holdout"] = hold_cal
            metrics["expectancy_R"] = hold_cal.get("expectancy_R")
            metrics["hit_rate"] = hold_cal.get("hit_rate")
            metrics["n_signals"] = hold_cal.get("n_signals")
            metrics["auc"] = hold_cal.get("auc", metrics.get("auc"))
            metrics["brier"] = hold_cal.get("brier", metrics.get("brier"))
    if keep_cal:
        prod["calibrator"] = final["calibrator"]
    else:
        prod["calibrator"] = None
        if hold_raw:
            decision_threshold = thr_raw
            metrics["holdout"] = {**hold_raw, "decision_threshold": thr_raw}
            metrics["expectancy_R"] = hold_raw.get("expectancy_R")
            metrics["hit_rate"] = hold_raw.get("hit_rate")
            metrics["n_signals"] = hold_raw.get("n_signals")
            metrics["auc"] = hold_raw.get("auc", metrics.get("auc"))
            metrics["brier"] = hold_raw.get("brier", metrics.get("brier"))
    prod["decision_threshold"] = float(decision_threshold)
    metrics["decision_threshold"] = float(decision_threshold)
    metrics["train_start"] = dataset.index.min().date() if len(dataset) else date.today()
    metrics["train_end"] = dataset.index.max().date() if len(dataset) else date.today()
    metrics["n_rows"] = len(dataset)
    metrics["train_rows"] = len(train_df)
    metrics["test_rows"] = len(test_df)
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
            "class_weight": "balanced",
        }
        scores = []
        for train_idx, test_idx in purged_walk_forward_splits(dataset.index, n_splits=3, embargo=5):
            if y.iloc[train_idx].nunique() < 2:
                continue
            bundle = fit_lgbm(X.iloc[train_idx], y.iloc[train_idx], params=params)
            prob = predict_proba(bundle, X.iloc[test_idx])
            _, fold_m = best_threshold(y.iloc[test_idx], prob, min_signals=8)
            scores.append(float(fold_m.get("expectancy_R") or 0.0))
        return float(np.mean(scores)) if scores else 0.0

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best = {**DEFAULT_LGBM_PARAMS, **study.best_params}
    return best
