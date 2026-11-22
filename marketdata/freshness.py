from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MAX_AGE_SEC = {
    "yfinance": 1200,
    "yfinance_daily": 86_400 * 3,  # daily bars = last session close is OK
    "nse_wrapper": 1200,
    "nse_eod": 86_400,
    "live_optional": 90,
}


class SignalBlocked(Exception):
    pass


def is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return time(9, 15) <= t <= time(15, 30)


def last_session_close(now: datetime | None = None) -> datetime:
    """Approximate previous NSE cash close (15:30 IST), skipping weekends."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    d = now.date()
    close_today = datetime.combine(d, time(15, 30), tzinfo=IST)
    if now >= close_today and now.weekday() < 5:
        return close_today
    # walk back to previous weekday
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return datetime.combine(d, time(15, 30), tzinfo=IST)


def assert_fresh(
    bar_ts: datetime,
    source: str,
    now: datetime | None = None,
    *,
    interval: str = "1d",
) -> timedelta:
    """Block only when data is older than the last session (daily) or max age (intraday)."""
    now = now or datetime.now(IST)
    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.replace(tzinfo=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    age = now - bar_ts.astimezone(now.tzinfo)

    # Daily / EOD: accept bars through previous session close (+1 day holiday slack)
    if interval in {"1d", "d", "day"} or source in {"nse_eod", "yfinance_daily"}:
        expected = last_session_close(now)
        # Allow bar date == expected session day (ignore intraday clock on daily candle)
        bar_day = bar_ts.astimezone(IST).date()
        expected_day = expected.date()
        # holiday slack: up to 3 calendar days behind last session
        if bar_day >= expected_day - timedelta(days=3):
            return age
        if is_market_open(now):
            raise SignalBlocked(
                f"{source} daily feed stalled: newest bar {bar_day} "
                f"(expected around {expected_day})"
            )
        return age

    max_age = MAX_AGE_SEC.get(source, 1200)
    if is_market_open(now) and age > timedelta(seconds=max_age):
        raise SignalBlocked(f"{source} feed stalled: newest bar is {age} old")
    return age
