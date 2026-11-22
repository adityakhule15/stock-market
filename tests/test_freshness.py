from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from marketdata.freshness import SignalBlocked, assert_fresh, is_market_open

IST = ZoneInfo("Asia/Kolkata")


def test_assert_fresh_blocks_stale_intraday_in_session():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=IST)  # Wednesday
    assert is_market_open(now)
    bar_ts = now - timedelta(minutes=25)
    with pytest.raises(SignalBlocked):
        assert_fresh(bar_ts, "yfinance", now=now, interval="5m")


def test_assert_fresh_allows_daily_previous_session():
    # Wed morning: Tue 15:30 close is fine for daily bars
    now = datetime(2026, 11, 11, 9, 50, tzinfo=IST)
    bar_ts = datetime(2026, 11, 10, 15, 30, tzinfo=IST)
    age = assert_fresh(bar_ts, "yfinance_daily", now=now, interval="1d")
    assert age.total_seconds() > 0


def test_assert_fresh_allows_premarket_stale():
    now = datetime(2026, 11, 11, 8, 50, tzinfo=IST)
    bar_ts = now - timedelta(hours=18)
    age = assert_fresh(bar_ts, "yfinance", now=now, interval="5m")
    assert age.total_seconds() > 0
