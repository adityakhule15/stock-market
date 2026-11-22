import pandas as pd

from config import trading_config as cfg
from scanner.orb import opening_range, orb_plan


def test_opening_range_uses_915_to_929():
    idx = pd.date_range("2026-11-11 09:15", periods=6, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104, 105],
            "high": [101, 102, 103, 110, 105, 106],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "volume": [1000, 1000, 1000, 5000, 1000, 1000],
        },
        index=idx,
    )
    hi, lo, vol = opening_range(df)
    # 09:15, 09:20, 09:25 only (through 09:29) — not 09:30
    assert hi == 103
    assert lo == 99
    assert vol == 3000


def test_orb_respects_intraday_flag():
    idx = pd.date_range("2026-11-11 09:15", periods=3, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame(
        {"open": 100, "high": 101, "low": 99.5, "close": 100.5, "volume": 5000},
        index=idx,
    )
    # When disabled, orb_plan returns None
    class _Off:
        INTRADAY_ENABLED = False
        ORB_MIN_WIDTH = cfg.ORB_MIN_WIDTH
        ORB_MAX_WIDTH = cfg.ORB_MAX_WIDTH
        ORB_VOL_MULT = cfg.ORB_VOL_MULT

    assert orb_plan("TEST.NS", df, 1000, 99, True, cfg_mod=_Off) is None
    assert cfg.INTRADAY_ENABLED is True
