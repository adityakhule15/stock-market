from __future__ import annotations

import numpy as np
import pandas as pd

from features.indicators import atr


def triple_barrier(
    df: pd.DataFrame,
    atr_mult_sl: float = 1.5,
    rr: float = 2.0,
    horizon: int = 5,
) -> pd.Series:
    """1 if +rr*R hit before -1R within horizon bars, else 0. R = atr_mult_sl * ATR."""
    a = atr(df)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    atr_v = a.values
    n = len(df)
    labels = np.full(n, np.nan)
    for i in range(n - 1):
        if np.isnan(atr_v[i]) or atr_v[i] <= 0:
            continue
        entry = close[i]
        R = atr_mult_sl * atr_v[i]
        stop = entry - R
        target = entry + rr * R
        hit = 0.0
        end = min(n, i + 1 + horizon)
        for j in range(i + 1, end):
            # Conservative: if both touched in same bar, stop wins
            if low[j] <= stop:
                hit = 0.0
                break
            if high[j] >= target:
                hit = 1.0
                break
        else:
            hit = 0.0
        labels[i] = hit
    return pd.Series(labels, index=df.index, name="y")
