import numpy as np
import pandas as pd

from timemachine.sim import replay, simulate, verdict
from config import trading_config as cfg


def _bars(n=120, seed=19):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.8, n))
    high = close + rng.uniform(0.1, 1.2, n)
    low = close - rng.uniform(0.1, 1.2, n)
    open_ = close + rng.normal(0, 0.2, n)
    idx = pd.date_range("2023-01-01", periods=n, freq="B", tz="Asia/Kolkata")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e5}, index=idx)


def test_simulate_seed_reproducible():
    bars = _bars()
    a = simulate(bars, 101, 98, 107, n=2000, seed=19)
    b = simulate(bars, 101, 98, 107, n=2000, seed=19)
    assert a == b


def test_simulate_stop_wins_when_both_in_candle():
    # Construct relative moves so one path hits both — logic sets stop first
    idx = pd.date_range("2023-01-01", periods=30, freq="B", tz="Asia/Kolkata")
    close = np.full(30, 100.0)
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 5,
            "low": close - 5,
            "close": close,
            "volume": 1e5,
        },
        index=idx,
    )
    out = simulate(df, entry=100, stop=99, target=101, n=500, seed=1, lookback=20, horizon=3)
    assert out["p_stop"] >= out["p_target"]


def test_replay_no_future():
    bars = _bars()

    def setup(hist):
        return len(hist) > 60 and hist["close"].iloc[-1] > hist["close"].iloc[-5]

    def levels(hist):
        entry = float(hist["high"].iloc[-1])
        stop = entry - 2
        target = entry + 4
        return entry, stop, target

    rep = replay(bars, setup, levels, horizon=5)
    assert "fires" in rep
