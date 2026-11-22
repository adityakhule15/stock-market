from __future__ import annotations

import pandas as pd

from features.indicators import add_indicators
from features.labels import triple_barrier

FEATURE_COLS = [
    "ret_1",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_20",
    "gap_pct",
    "overnight_ret",
    "intraday_ret",
    "ema9_dist",
    "ema20_dist",
    "ema50_dist",
    "ema200_dist",
    "ema20_slope",
    "adx",
    "rsi14",
    "macd_hist",
    "stoch",
    "roc10",
    "atr_pct",
    "bb_pctb",
    "bb_bw",
    "rvol20",
    "vol_z",
    "obv_slope",
    "dist_20d_high",
    "dist_20d_low",
    "dist_52w_high",
    "inside_day",
    "nr7",
    "dow",
]


def build_features(df: pd.DataFrame, market_ctx: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build features available at signal time — shift by 1 bar (no look-ahead)."""
    ind = add_indicators(df)
    feats = ind[FEATURE_COLS].copy()
    if market_ctx is not None and not market_ctx.empty:
        m = market_ctx.reindex(ind.index).ffill()
        for col in m.columns:
            feats[col] = m[col]
    # Shift so row t only uses info known at close of t-1 / available at t decision without future
    feats = feats.shift(1)
    return feats


def build_dataset(
    df: pd.DataFrame,
    market_ctx: pd.DataFrame | None = None,
    atr_mult_sl: float = 1.35,
    rr: float = 1.5,
    horizon: int = 8,
) -> pd.DataFrame:
    feats = build_features(df, market_ctx=market_ctx)
    y = triple_barrier(df, atr_mult_sl=atr_mult_sl, rr=rr, horizon=horizon)
    out = feats.copy()
    out["y"] = y
    # Attach raw context useful for orders
    ind = add_indicators(df)
    out["close"] = df["close"]
    out["high"] = df["high"]
    out["low"] = df["low"]
    out["atr14"] = ind["atr14"]
    out["ema50"] = ind["ema50"]
    out["prev_high"] = df["high"].shift(1)
    out["prev_low"] = df["low"].shift(1)
    out = out.dropna(subset=["y"] + [c for c in FEATURE_COLS if c in out.columns])
    return out


def feature_matrix(dataset: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    cols = [c for c in FEATURE_COLS if c in dataset.columns]
    extra = [c for c in dataset.columns if c.startswith("nifty_") or c.startswith("vix_")]
    cols = cols + extra
    X = dataset[cols].replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    y = dataset["y"].astype(int)
    return X, y
