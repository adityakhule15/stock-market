"""Feature + label builders for index_pattern_lgbm."""

from __future__ import annotations

import numpy as np
import pandas as pd

from features.builders import FEATURE_COLS, build_features
from features.indicators import atr, ema, rsi
from indexdesk.patterns import detect_index_patterns, pattern_feature_flags

INDEX_EXTRA_COLS = [
    "pat_orb_break",
    "pat_ema_stack_bull",
    "pat_ema_stack_bear",
    "pat_vwap_reclaim",
    "pat_vwap_reject",
    "pat_bull_engulf",
    "pat_bear_engulf",
    "pat_rsi_oversold_bounce",
    "pat_rsi_overbought_fade",
    "pat_inside_bar_break",
    "pat_nr7_break",
    "pat_bull_count",
    "pat_bear_count",
    "pat_max_conf",
]

INDEX_FEATURE_COLS = list(FEATURE_COLS) + INDEX_EXTRA_COLS


def _forward_up_label(df: pd.DataFrame, horizon: int = 3, atr_mult: float = 0.35) -> pd.Series:
    """1 if forward path favors upside vs downside within horizon days."""
    c = df["close"].astype(float).to_numpy()
    a = atr(df, 14).astype(float).to_numpy()
    y = np.full(len(df), np.nan)
    for i in range(len(df) - horizon - 1):
        entry = float(c[i])
        sl_dist = float(a[i] or 0) * atr_mult
        if sl_dist <= 0 or not np.isfinite(sl_dist):
            continue
        up = entry + sl_dist
        dn = entry - sl_dist
        future = c[i + 1 : i + 1 + horizon]
        hit_up = bool(np.any(future >= up))
        hit_dn = bool(np.any(future <= dn))
        if hit_up and not hit_dn:
            y[i] = 1.0
        elif hit_dn and not hit_up:
            y[i] = 0.0
        elif hit_up and hit_dn:
            up_i = int(np.argmax(future >= up))
            dn_i = int(np.argmax(future <= dn))
            y[i] = 1.0 if up_i <= dn_i else 0.0
        else:
            y[i] = 1.0 if float(future[-1]) > entry else 0.0
    return pd.Series(y, index=df.index)


def _vectorized_pattern_flags(daily: pd.DataFrame) -> pd.DataFrame:
    """Fast daily proxies for pattern flags (training). Live path uses full detectors."""
    c = daily["close"].astype(float)
    o = daily["open"].astype(float)
    h = daily["high"].astype(float)
    lo = daily["low"].astype(float)
    e9, e20, e50 = ema(c, 9), ema(c, 20), ema(c, 50)
    r = rsi(c, 14)
    ranges = (h - lo).astype(float)

    bull_eng = (c > o) & (o <= c.shift(1)) & (c >= o.shift(1)) & ((c - o) > (c.shift(1) - o.shift(1)).abs())
    bear_eng = (c < o) & (o >= c.shift(1)) & (c <= o.shift(1)) & ((o - c) > (c.shift(1) - o.shift(1)).abs())
    rsi_bounce = (r.shift(1) < 30) & (r > r.shift(1)) & (c > c.shift(1))
    rsi_fade = (r.shift(1) > 70) & (r < r.shift(1)) & (c < c.shift(1))
    inside_prev = (h.shift(1) <= h.shift(2)) & (lo.shift(1) >= lo.shift(2))
    nr7_prev = ranges.shift(1) <= ranges.shift(1).rolling(7).min()
    broke_up = c > h.shift(1)
    broke_dn = c < lo.shift(1)

    ema_bull = (e9 > e20) & (e20 > e50) & (c > e9)
    ema_bear = (e9 < e20) & (e20 < e50) & (c < e9)

    out = pd.DataFrame(index=daily.index)
    out["pat_orb_break"] = 0.0  # needs intraday — filled at live score
    out["pat_ema_stack_bull"] = ema_bull.astype(float) * 0.62
    out["pat_ema_stack_bear"] = ema_bear.astype(float) * 0.62
    out["pat_vwap_reclaim"] = 0.0
    out["pat_vwap_reject"] = 0.0
    out["pat_bull_engulf"] = bull_eng.astype(float) * 0.58
    out["pat_bear_engulf"] = bear_eng.astype(float) * 0.58
    out["pat_rsi_oversold_bounce"] = rsi_bounce.astype(float) * 0.55
    out["pat_rsi_overbought_fade"] = rsi_fade.astype(float) * 0.55
    out["pat_inside_bar_break"] = ((inside_prev & (broke_up | broke_dn)).astype(float) * 0.57)
    out["pat_nr7_break"] = ((nr7_prev & (broke_up | broke_dn)).astype(float) * 0.56)
    bull_cols = [
        "pat_ema_stack_bull",
        "pat_bull_engulf",
        "pat_rsi_oversold_bounce",
    ]
    bear_cols = [
        "pat_ema_stack_bear",
        "pat_bear_engulf",
        "pat_rsi_overbought_fade",
    ]
    out["pat_bull_count"] = (out[bull_cols] > 0).sum(axis=1).astype(float)
    out["pat_bear_count"] = (out[bear_cols] > 0).sum(axis=1).astype(float)
    out["pat_max_conf"] = out[INDEX_EXTRA_COLS[:-3]].max(axis=1)
    return out.fillna(0.0)


def build_index_dataset(daily: pd.DataFrame, *, horizon: int = 3) -> pd.DataFrame:
    """Daily dataset with technicals + pattern flags + y."""
    if daily is None or len(daily) < 120:
        return pd.DataFrame()
    feats = build_features(daily, market_ctx=None)
    pat_df = _vectorized_pattern_flags(daily)
    out = feats.join(pat_df, how="left")
    out["y"] = _forward_up_label(daily, horizon=horizon)
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=["y"])
    out["y"] = out["y"].astype(int)
    return out


def build_index_live_row(
    daily: pd.DataFrame,
    *,
    bars_5m: pd.DataFrame | None = None,
    bars_1m: pd.DataFrame | None = None,
    spot: float | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """Single feature row + pattern hits for live scoring."""
    if daily is None or daily.empty:
        return pd.DataFrame(), []
    hits = detect_index_patterns(daily=daily, bars_5m=bars_5m, bars_1m=bars_1m, spot=spot)
    feats = build_features(daily, market_ctx=None)
    if feats.empty:
        return pd.DataFrame(), hits
    row = feats.iloc[[-1]].copy()
    flags = pattern_feature_flags(hits)
    for k, v in flags.items():
        row[k] = v
    row = row.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    for c in INDEX_FEATURE_COLS:
        if c not in row.columns:
            row[c] = 0.0
    return row[INDEX_FEATURE_COLS], hits


def feature_matrix_index(dataset: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    cols = [c for c in INDEX_FEATURE_COLS if c in dataset.columns]
    X = dataset[cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = dataset["y"].astype(int)
    return X, y
