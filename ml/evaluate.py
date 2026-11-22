from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def purged_walk_forward_splits(
    index: pd.DatetimeIndex,
    n_splits: int = 5,
    embargo: int = 5,
):
    """Yield (train_idx, test_idx) integer positions with embargo gap (row-based)."""
    n = len(index)
    fold = n // (n_splits + 1)
    for i in range(1, n_splits + 1):
        test_start = fold * i
        test_end = min(n, fold * (i + 1))
        train_end = max(0, test_start - embargo)
        if train_end < 50 or test_end - test_start < 10:
            continue
        yield np.arange(0, train_end), np.arange(test_start, test_end)


def date_walk_forward_splits(
    index: pd.DatetimeIndex,
    *,
    n_splits: int = 5,
    embargo_days: int = 8,
):
    """Date-based walk-forward: gap of at least `embargo_days` calendar trading days.

    When many symbols share dates, row-based embargo under-purges. This splits by
    unique dates instead.
    """
    dates = pd.DatetimeIndex(pd.to_datetime(index).normalize().unique()).sort_values()
    if len(dates) < n_splits + 2:
        # Fall back to row splits
        yield from purged_walk_forward_splits(index, n_splits=n_splits, embargo=embargo_days)
        return
    fold = len(dates) // (n_splits + 1)
    norm = pd.to_datetime(index).normalize()
    for i in range(1, n_splits + 1):
        test_start_d = dates[fold * i]
        test_end_i = min(len(dates), fold * (i + 1))
        test_end_d = dates[test_end_i - 1]
        # Embargo: last train date must be embargo_days before test_start
        train_cut_pos = fold * i - embargo_days
        if train_cut_pos < 10:
            continue
        train_end_d = dates[train_cut_pos]
        train_mask = norm <= train_end_d
        test_mask = (norm >= test_start_d) & (norm <= test_end_d)
        train_idx = np.where(train_mask)[0]
        test_idx = np.where(test_mask)[0]
        if len(train_idx) < 50 or len(test_idx) < 10:
            continue
        yield train_idx, test_idx


def classification_metrics(y_true, y_prob, threshold: float = 0.6) -> dict:
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    if len(y_true) < 2 or len(np.unique(y_true)) < 2:
        return {
            "auc": 0.5,
            "brier": float(np.mean((y_prob - y_true) ** 2)),
            "log_loss": float(log_loss(y_true, np.clip(y_prob, 1e-6, 1 - 1e-6), labels=[0, 1]))
            if len(y_true)
            else 1.0,
            "hit_rate": float(np.mean(y_true[y_prob >= threshold])) if np.any(y_prob >= threshold) else 0.0,
            "n_signals": int(np.sum(y_prob >= threshold)),
        }
    preds = y_prob >= threshold
    return {
        "auc": float(roc_auc_score(y_true, y_prob)),
        "brier": float(brier_score_loss(y_true, y_prob)),
        "log_loss": float(log_loss(y_true, np.clip(y_prob, 1e-6, 1 - 1e-6), labels=[0, 1])),
        "hit_rate": float(np.mean(y_true[preds])) if preds.any() else 0.0,
        "n_signals": int(preds.sum()),
    }


def expectancy_R(y_true, y_prob, threshold: float = 0.6, win_R: float = 2.0, loss_R: float = 1.0) -> float:
    mask = np.asarray(y_prob) >= threshold
    if not mask.any():
        return 0.0
    y = np.asarray(y_true)[mask]
    wins = np.mean(y)
    return float(wins * win_R - (1 - wins) * loss_R)


def reliability_table(y_true, y_prob, buckets: int = 5) -> list[dict]:
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    edges = np.linspace(0, 1, buckets + 1)
    rows = []
    for i in range(buckets):
        lo, hi = edges[i], edges[i + 1]
        mask = (y_prob >= lo) & (y_prob < hi if i < buckets - 1 else y_prob <= hi)
        if not mask.any():
            continue
        rows.append(
            {
                "prob_lo": round(float(lo), 2),
                "prob_hi": round(float(hi), 2),
                "n": int(mask.sum()),
                "mean_pred": round(float(y_prob[mask].mean()), 3),
                "actual_win_rate": round(float(y_true[mask].mean()), 3),
            }
        )
    return rows


def best_threshold(
    y_true,
    y_prob,
    *,
    grid: tuple[float, ...] | None = None,
    min_signals: int = 15,
    win_R: float = 2.0,
    loss_R: float = 1.0,
) -> tuple[float, dict]:
    """Pick probability threshold that maximizes expectancy with enough trades."""
    grid = grid or tuple(round(x, 2) for x in np.arange(0.52, 0.85, 0.02))
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    candidates: list[tuple[float, dict]] = []
    for t in grid:
        m = classification_metrics(y_true, y_prob, threshold=float(t))
        m["expectancy_R"] = expectancy_R(y_true, y_prob, threshold=float(t), win_R=win_R, loss_R=loss_R)
        if m["n_signals"] < max(5, min_signals // 2):
            continue
        candidates.append((float(t), m))
    if not candidates:
        m = classification_metrics(y_true, y_prob, threshold=0.60)
        m["expectancy_R"] = expectancy_R(y_true, y_prob, threshold=0.60, win_R=win_R, loss_R=loss_R)
        return 0.60, m

    pos = [(t, m) for t, m in candidates if m["expectancy_R"] >= 0 and m["n_signals"] >= min_signals]
    if pos:
        t, m = max(pos, key=lambda kv: (kv[1]["expectancy_R"], kv[1].get("hit_rate", 0), -kv[1]["n_signals"]))
        return t, m
    pos2 = [(t, m) for t, m in candidates if m["expectancy_R"] >= 0]
    if pos2:
        t, m = max(pos2, key=lambda kv: (kv[1]["expectancy_R"], kv[1].get("hit_rate", 0)))
        return t, m
    selective = [(t, m) for t, m in candidates if m["n_signals"] <= max(min_signals * 3, 40)]
    pool = selective or candidates
    t, m = max(pool, key=lambda kv: (kv[1].get("hit_rate", 0), kv[1]["expectancy_R"], -kv[1]["n_signals"]))
    return t, m


def evaluate_walk_forward(
    bundle: dict,
    dataset: pd.DataFrame,
    last_n_sessions: int = 60,
    threshold: float | None = None,
) -> dict:
    """Evaluate on a *held-out* dataset only — caller must not pass train rows."""
    from features.builders import feature_matrix
    from ml.train import predict_proba

    tail = dataset.iloc[-last_n_sessions:] if len(dataset) > last_n_sessions else dataset
    X, y = feature_matrix(tail)
    if len(X) == 0 or y.nunique() < 2:
        return {
            "auc": 0.5,
            "brier": 1.0,
            "log_loss": 1.0,
            "hit_rate": 0.0,
            "n_signals": 0,
            "expectancy_R": 0.0,
            "max_dd": 0.0,
            "decision_threshold": float(bundle.get("decision_threshold") or 0.60),
        }
    prob = predict_proba(bundle, X)
    thr = threshold if threshold is not None else float(bundle.get("decision_threshold") or 0.60)
    m = classification_metrics(y, prob, threshold=thr)
    m["expectancy_R"] = expectancy_R(y, prob, threshold=thr, win_R=2.0)
    m["decision_threshold"] = thr
    m["reliability"] = reliability_table(y.values, prob)
    pnl = []
    for yt, p in zip(y.values, prob):
        if p < thr:
            continue
        pnl.append(2.0 if yt == 1 else -1.0)
    if pnl:
        eq = np.cumsum(pnl)
        peak = np.maximum.accumulate(eq)
        dd = eq - peak
        m["max_dd"] = float(abs(dd.min()))
    else:
        m["max_dd"] = 0.0
    # Base-rate log-loss for comparison
    base_p = float(y.mean())
    base_prob = np.full(len(y), base_p)
    m["baseline_log_loss"] = float(log_loss(y, np.clip(base_prob, 1e-6, 1 - 1e-6), labels=[0, 1]))
    return m
