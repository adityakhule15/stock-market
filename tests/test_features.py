import numpy as np
import pandas as pd

from features.builders import build_features
from features.labels import triple_barrier


def _ohlcv(n=80, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.2, 1.5, n)
    low = close - rng.uniform(0.2, 1.5, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.integers(1000, 5000, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="B", tz="Asia/Kolkata")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def test_no_lookahead_features():
    df = _ohlcv(100)
    full = build_features(df)
    truncated = build_features(df.iloc[:-10])
    common = full.index.intersection(truncated.index)
    cols = [c for c in truncated.columns if c in full.columns]
    a = full.loc[common, cols].fillna(0)
    b = truncated.loc[common, cols].fillna(0)
    assert np.allclose(a.values, b.values, equal_nan=True)


def test_triple_barrier_handmade():
    # Enough history for ATR(14), then a clean target hit before stop
    n = 40
    idx = pd.date_range("2024-01-01", periods=n, freq="B", tz="Asia/Kolkata")
    close = np.linspace(100, 105, n)
    high = close + 1.0
    low = close - 1.0
    # After ATR warms up, force a strong upside bar that hits +1R first
    high[-3] = close[-4] + 5.0
    low[-3] = close[-4] - 0.2
    df = pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )
    y = triple_barrier(df, atr_mult_sl=1.0, rr=1.0, horizon=5)
    assert y.notna().any()
    assert set(y.dropna().unique()).issubset({0.0, 1.0})
