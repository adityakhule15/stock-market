from datetime import datetime
from zoneinfo import ZoneInfo

from signals.gate import PortfolioState, risk_gate

IST = ZoneInfo("Asia/Kolkata")


def _order(**kw):
    # Defaults fit current trading_config: RISK_PER_TRADE=1% of ₹2000 → ₹20,
    # MAX_POSITION_PCT=30% → ₹600.
    o = {
        "symbol": "TEST.NS",
        "strategy": "swing",
        "max_loss": 20,
        "capital_used": 600,
        "rr_net": 2.1,
        "qty": 7,
        "probability": 0.7,
    }
    o.update(kw)
    return o


def test_each_check_can_block():
    now = datetime(2026, 11, 11, 10, 0, tzinfo=IST)
    tm = {"verdict": "EDGE"}
    base_pf = PortfolioState(avg_volumes={"TEST.NS": 1e9})

    cases = [
        (PortfolioState(kill_switch=True, avg_volumes={"TEST.NS": 1e9}), "kill switch"),
        (base_pf, "max loss", {"max_loss": 100}),
        (PortfolioState(day_pnl=-100, avg_volumes={"TEST.NS": 1e9}), "daily loss"),
        (PortfolioState(week_pnl=-200, avg_volumes={"TEST.NS": 1e9}), "weekly loss"),
        (PortfolioState(trades_today=2, avg_volumes={"TEST.NS": 1e9}), "too many trades"),
        (PortfolioState(open_positions=2, avg_volumes={"TEST.NS": 1e9}), "max open"),
        (PortfolioState(free_cash=100, avg_volumes={"TEST.NS": 1e9}), "position too big"),
        (base_pf, "net reward", {"rr_net": 1.0}),
        (base_pf, "data too old", None, 30),
        (base_pf, "Time Machine", None, 5, {"verdict": "COIN_FLIP"}),
        (PortfolioState(avg_volumes={"TEST.NS": 100}), "liquidity", {"qty": 5}),
    ]

    # kill switch
    g = risk_gate(_order(), PortfolioState(kill_switch=True, avg_volumes={"TEST.NS": 1e9}), tm, 5, now=now)
    assert not g.passed and any("kill" in r for r in g.reasons)

    g = risk_gate(_order(max_loss=100), base_pf, tm, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(), PortfolioState(day_pnl=-100, avg_volumes={"TEST.NS": 1e9}), tm, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(), PortfolioState(week_pnl=-200, avg_volumes={"TEST.NS": 1e9}), tm, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(), PortfolioState(trades_today=2, avg_volumes={"TEST.NS": 1e9}), tm, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(), PortfolioState(open_positions=2, avg_volumes={"TEST.NS": 1e9}), tm, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(), PortfolioState(free_cash=100, avg_volumes={"TEST.NS": 1e9}), tm, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(rr_net=1.0), base_pf, tm, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(), base_pf, tm, 30, now=now)
    assert not g.passed

    g = risk_gate(_order(), base_pf, {"verdict": "COIN_FLIP"}, 5, now=now)
    assert not g.passed

    g = risk_gate(_order(qty=5), PortfolioState(avg_volumes={"TEST.NS": 100}), tm, 5, now=now)
    assert not g.passed


def test_pass_when_clear():
    now = datetime(2026, 11, 11, 10, 0, tzinfo=IST)
    g = risk_gate(
        _order(),
        PortfolioState(avg_volumes={"TEST.NS": 1e9}, free_cash=2000),
        {"verdict": "EDGE"},
        5,
        now=now,
    )
    assert g.passed
