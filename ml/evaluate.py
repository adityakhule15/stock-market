from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score


def purged_walk_forward_splits(
    index: pd.DatetimeIndex,
    n_splits: int = 5,
    embargo: int = 5,
):
    """Yield (train_idx, test_idx) integer positions with embargo gap."""
    n = len(index)
    fold = n // (n_splits + 1)
    for i in range(1, n_splits + 1):
        test_start = fold * i
        test_end = min(n, fold * (i + 1))
        train_end = max(0, test_start - embargo)
        if train_end < 50 or test_end - test_start < 10:
            continue
        yield np.arange(0, train_end), np.arange(test_start, test_end)


def classification_metrics(y_true, y_prob, threshold: float = 0.6) -> dict:
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    if len(y_true) < 2 or len(np.unique(y_true)) < 2:
        return {
            "auc": 0.5,
            "brier": float(np.mean((y_prob - y_true) ** 2)),
            "hit_rate": float(np.mean(y_true[y_prob >= threshold])) if np.any(y_prob >= threshold) else 0.0,
            "n_signals": int(np.sum(y_prob >= threshold)),
        }
    preds = y_prob >= threshold
    return {
        "auc": float(roc_auc_score(y_true, y_prob)),
        "brier": float(brier_score_loss(y_true, y_prob)),
        "hit_rate": float(np.mean(y_true[preds])) if preds.any() else 0.0,
        "n_signals": int(preds.sum()),
    }


def expectancy_R(y_true, y_prob, threshold: float = 0.6, win_R: float = 1.5, loss_R: float = 1.0) -> float:
    mask = np.asarray(y_prob) >= threshold
    if not mask.any():
        return 0.0
    y = np.asarray(y_true)[mask]
    wins = np.mean(y)
    return float(wins * win_R - (1 - wins) * loss_R)


def best_threshold(
    y_true,
    y_prob,
    *,
    grid: tuple[float, ...] | None = None,
    min_signals: int = 15,
    win_R: float = 1.5,
    loss_R: float = 1.0,
) -> tuple[float, dict]:
    """Pick probability threshold that maximizes expectancy with enough trades.

    Prefer thresholds with non-negative expectancy; among losers, prefer higher
    hit-rate at fewer/higher-confidence signals (capital preservation).
    """
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

    # 1) Best among positive expectancy with enough signals
    pos = [(t, m) for t, m in candidates if m["expectancy_R"] >= 0 and m["n_signals"] >= min_signals]
    if pos:
        t, m = max(pos, key=lambda kv: (kv[1]["expectancy_R"], kv[1].get("hit_rate", 0), -kv[1]["n_signals"]))
        return t, m
    # 2) Any non-negative expectancy
    pos2 = [(t, m) for t, m in candidates if m["expectancy_R"] >= 0]
    if pos2:
        t, m = max(pos2, key=lambda kv: (kv[1]["expectancy_R"], kv[1].get("hit_rate", 0)))
        return t, m
    # 3) Highest hit-rate among selective thresholds (n_signals capped)
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
    from features.builders import feature_matrix

    tail = dataset.iloc[-last_n_sessions:] if len(dataset) > last_n_sessions else dataset
    X, y = feature_matrix(tail)
    model = bundle["model"]
    cal = bundle.get("calibrator")
    raw = model.predict_proba(X)[:, 1]
    prob = cal.transform(raw) if cal is not None else raw
    thr = threshold if threshold is not None else float(bundle.get("decision_threshold") or 0.60)
    m = classification_metrics(y, prob, threshold=thr)
    m["expectancy_R"] = expectancy_R(y, prob, threshold=thr)
    m["decision_threshold"] = thr
    # Rough max DD proxy from signal sequence (±R)
    pnl = []
    for yt, p in zip(y.values, prob):
        if p < thr:
            continue
        pnl.append(1.5 if yt == 1 else -1.0)
    if pnl:
        eq = np.cumsum(pnl)
        peak = np.maximum.accumulate(eq)
        dd = eq - peak
        m["max_dd"] = float(abs(dd.min()))
    else:
        m["max_dd"] = 0.0
    return m
