"""Suggested SL must never widen; F&O square-off forces EXIT_NOW."""

from datetime import datetime
from zoneinfo import ZoneInfo

from portfolio.management import suggest_management, tighten_stop

IST = ZoneInfo("Asia/Kolkata")


def test_tighten_stop_buy_never_widens():
    assert tighten_stop(100.0, 99.0, "BUY") == 100.0
    assert tighten_stop(100.0, 101.0, "BUY") == 101.0


def test_tighten_stop_sell_never_widens():
    assert tighten_stop(100.0, 101.0, "SELL") == 100.0
    assert tighten_stop(100.0, 99.0, "SELL") == 99.0


def test_suggest_management_sl_never_wider_than_current():
    # +1.5R should trail but not below current stop for BUY... trail raises SL
    out = suggest_management(
        entry=100,
        stop=95,
        target=110,
        mark=108,
        side="BUY",
        atr=1.0,
        now=datetime(2026, 11, 11, 11, 0, tzinfo=IST),
    )
    assert out["suggested_sl"] >= 95
    assert out["sl_widened"] is False
    # Deep drawdown still cannot propose wider (lower) SL
    out2 = suggest_management(
        entry=100,
        stop=95,
        target=110,
        mark=96,
        side="BUY",
        atr=5.0,
        now=datetime(2026, 11, 11, 11, 0, tzinfo=IST),
    )
    assert out2["suggested_sl"] >= 95


def test_fno_exit_now_after_1510():
    now = datetime(2026, 11, 11, 15, 12, tzinfo=IST)
    fno = suggest_management(
        entry=10, stop=6, target=16, mark=11, side="BUY", is_option=True, now=now
    )
    assert fno["action"] == "EXIT_NOW"
    cnc = suggest_management(
        entry=100, stop=95, target=110, mark=102, side="BUY", is_option=False, now=now
    )
    assert cnc["action"] != "EXIT_NOW"


def test_adverse_r_exits_before_far_stop():
    """BANKNIFTY-like: LTP well below entry but still above SL → EXIT_NOW, not HOLD."""
    now = datetime(2026, 11, 20, 13, 40, tzinfo=IST)
    out = suggest_management(
        entry=24.25,
        stop=16.28,
        target=40.08,
        mark=18.30,
        side="BUY",
        is_option=True,
        now=now,
    )
    assert out["r_multiple"] <= -0.5
    assert out["action"] == "EXIT_NOW"
    assert "adverse" in out["reason"].lower() or "premium" in out["reason"].lower()


def test_fno_premium_drop_exit():
    now = datetime(2026, 11, 20, 11, 0, tzinfo=IST)
    # 25% premium drop, mild R still above -0.5 if SL is very wide — premium rule catches it
    out = suggest_management(
        entry=20.0,
        stop=5.0,  # wide SL → R may be only -0.33
        target=40.0,
        mark=15.0,  # -25%
        side="BUY",
        is_option=True,
        now=now,
    )
    assert out["action"] == "EXIT_NOW"
