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
    """Build features available at signal time (close of bar t → decide for t+1).

    No extra shift: labels already require the *next* session for entry, so
    features on row t are known at the close of t.
    """
    ind = add_indicators(df)
    feats = ind[FEATURE_COLS].copy()
    if market_ctx is not None and not market_ctx.empty:
        m = market_ctx.reindex(ind.index).ffill()
        for col in m.columns:
            feats[col] = m[col]
    return feats


def build_feature_row(
    df: pd.DataFrame,
    market_ctx: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Single-row feature matrix for the latest completed bar (live scoring)."""
    feats = build_features(df, market_ctx=market_ctx)
    if feats.empty:
        return feats
    return feats.iloc[[-1]].replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)


def build_dataset(
    df: pd.DataFrame,
    market_ctx: pd.DataFrame | None = None,
    atr_mult_sl: float = 1.5,
    rr: float = 2.0,
    horizon: int = 5,
    *,
    include_market: bool = True,
) -> pd.DataFrame:
    if market_ctx is None and include_market:
        try:
            from features.market import build_market_ctx, load_market_frames, relative_strength

            nifty, vix = load_market_frames()
            market_ctx = build_market_ctx(df.index, nifty=nifty, vix=vix)
            rs = relative_strength(df["close"].astype(float), nifty)
            market_ctx = market_ctx.copy()
            market_ctx["rel_strength_20"] = rs
        except Exception:  # noqa: BLE001
            market_ctx = None
    feats = build_features(df, market_ctx=market_ctx)
    y = triple_barrier(df, atr_mult_sl=atr_mult_sl, rr=rr, horizon=horizon)
    out = feats.copy()
    out["y"] = y
    ind = add_indicators(df)
    out["close"] = df["close"]
    out["high"] = df["high"]
    out["low"] = df["low"]
    out["atr14"] = ind["atr14"]
    out["ema50"] = ind["ema50"]
    out["prev_high"] = df["high"].shift(1)
    out["prev_low"] = df["low"].shift(1)
    feat_cols = [c for c in FEATURE_COLS if c in out.columns]
    out = out.dropna(subset=["y"] + feat_cols)
    return out


def feature_matrix(dataset: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    cols = [c for c in FEATURE_COLS if c in dataset.columns]
    extra = [
        c
        for c in dataset.columns
        if c.startswith("nifty_") or c.startswith("vix_") or c.startswith("rel_") or c == "breadth_up"
    ]
    cols = cols + extra
    X = dataset[cols].replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    y = dataset["y"].astype(int)
    return X, y
