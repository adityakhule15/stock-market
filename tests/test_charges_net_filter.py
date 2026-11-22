"""net_ok=False ideas must never be ranked."""

from types import SimpleNamespace

from dashboard.ranking import rank_signals
from portfolio.charges import trade_economics


def test_trade_economics_net_ok_false_when_target_too_tight():
    # Tiny move eaten by charges
    eco = trade_economics(buy_price=400, sell_price=401, units=1, product="CNC", is_option=False)
    assert eco["net_ok"] is False


def test_rank_signals_drops_net_not_ok(monkeypatch):
    s = SimpleNamespace(
        capital_used=400,
        max_loss=20,
        probability=0.9,
        rr_net=2.0,
        entry_trigger=400,
        target1=401,
        qty=1,
        side="BUY",
        product="CNC",
        strategy="swing",
        instrument=SimpleNamespace(symbol="X.NS"),
        gate={"result": "PASS"},
        reasons={},
        time_machine={"verdict": "EDGE"},
        pipeline={},
        ticket_kind="normal",
    )
    ranked = rank_signals([s], wallet=2000, limit=5)
    assert ranked == []
