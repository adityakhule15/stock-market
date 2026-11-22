from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from config import trading_config as cfg
from config.trading_config import life_status_for_equity, risk_params_for_equity
from portfolio.models import PortfolioSettings
from portfolio.services import get_portfolio_state, mark_signal_taken, reset_life_status
from signals.engine import OrderContext, build_order
from signals.gate import PortfolioState, risk_gate
from signals.models import Signal
from marketdata.models import Instrument

IST = ZoneInfo("Asia/Kolkata")


def test_life_status_thresholds():
    assert life_status_for_equity(2000) == "alive"
    assert life_status_for_equity(1700) == "survival"
    assert life_status_for_equity(1600) == "survival"
    assert life_status_for_equity(800) == "dead"
    assert life_status_for_equity(0) == "dead"


def test_survival_risk_tighter_than_alive():
    alive = risk_params_for_equity(2000)
    surv = risk_params_for_equity(1600)
    assert surv.risk_per_trade < alive.risk_per_trade
    assert surv.min_prob > alive.min_prob
    assert surv.max_trades_per_day == 1


def test_dead_blocks_gate():
    now = datetime(2026, 11, 11, 10, 0, tzinfo=IST)
    o = {
        "symbol": "TEST.NS",
        "strategy": "swing",
        "max_loss": 20,
        "capital_used": 400,
        "rr_net": 2.1,
        "qty": 2,
        "probability": 0.7,
    }
    pf = PortfolioState(
        life_status="dead",
        equity=800,
        kill_switch=True,
        avg_volumes={"TEST.NS": 1e9},
        free_cash=800,
    )
    g = risk_gate(o, pf, {"verdict": "EDGE"}, 5, now=now)
    assert not g.passed
    assert any("DEAD" in r for r in g.reasons)


def test_floor_breach_blocks():
    now = datetime(2026, 11, 11, 10, 0, tzinfo=IST)
    # equity 900, max_loss 150 → 900-150=750 < DEAD_FLOOR 800
    o = {
        "symbol": "TEST.NS",
        "strategy": "swing",
        "max_loss": 150,
        "capital_used": 400,
        "rr_net": 2.1,
        "qty": 5,
        "probability": 0.7,
    }
    pf = PortfolioState(
        life_status="survival",
        equity=900,
        kill_switch=False,
        avg_volumes={"TEST.NS": 1e9},
        free_cash=900,
        risk_per_trade=0.01,
        max_position_pct=0.3,
        max_open_positions=1,
        max_trades_per_day=1,
        min_prob=0.65,
    )
    g = risk_gate(o, pf, {"verdict": "EDGE"}, 5, now=now)
    assert not g.passed
    assert any("DEAD_FLOOR" in r for r in g.reasons)


def test_build_order_respects_dead_floor_sizing(monkeypatch):
    # Zero-ish charges so edge filters pass; assert floor sizing holds
    def _eco(**kwargs):
        units = int(kwargs.get("units") or 1)
        buy = float(kwargs.get("buy_price") or 0)
        sell = float(kwargs.get("sell_price") or 0)
        gross = (sell - buy) * units
        return {
            "net_ok": True,
            "net_pnl": gross,
            "gross_pnl": gross,
            "charges_total": 0.0,
            "breakeven_sell": buy,
            "tip": "",
            "verdict": "NET_OK",
            "buy": {"total": 0.0},
            "sell": {"total": 0.0},
        }

    monkeypatch.setattr("portfolio.charges.trade_economics", _eco)
    ctx = OrderContext(prev_high=100.9, prev_low=99, atr=2.0, close=100, ema50=98)
    risk = risk_params_for_equity(2000)
    order = build_order("BUY", ctx, prob=0.7, risk=risk, capital=2000)
    assert order is not None
    assert 2000 - order["max_loss"] >= cfg.DEAD_FLOOR


def test_build_order_none_when_dead():
    ctx = OrderContext(prev_high=100.9, prev_low=99, atr=2.0, close=100, ema50=98)
    risk = risk_params_for_equity(500)
    assert risk.life_status == "dead"
    assert build_order("BUY", ctx, prob=0.9, risk=risk, capital=500) is None


@pytest.mark.django_db
def test_portfolio_goes_dead_and_blocks_taken():
    ps = PortfolioSettings.get_solo()
    ps.equity = Decimal("800")
    ps.cash = Decimal("800")
    ps.life_status = PortfolioSettings.LifeStatus.ALIVE
    ps.kill_switch = False
    ps.save()
    state = get_portfolio_state()
    assert state.life_status == "dead"
    assert state.kill_switch is True

    inst = Instrument.objects.create(symbol="DEADTEST.NS")
    from django.utils import timezone
    from datetime import timedelta

    sig = Signal.objects.create(
        instrument=inst,
        side="BUY",
        data_as_of=timezone.now(),
        data_source="yfinance",
        valid_until=timezone.now() + timedelta(days=1),
        entry_trigger=100,
        entry_limit=100.3,
        sl_trigger=97,
        sl_limit=96.7,
        target1=106,
        target2=109,
        qty=1,
        capital_used=100,
        max_loss=3,
        rr_net=2.0,
        probability=0.7,
        chase_limit=100.75,
    )
    with pytest.raises(ValueError, match="DEAD"):
        mark_signal_taken(sig, mode="paper")


@pytest.mark.django_db
def test_reset_life_staff_path():
    ps = PortfolioSettings.get_solo()
    ps.equity = Decimal("700")
    ps.life_status = PortfolioSettings.LifeStatus.DEAD
    ps.kill_switch = True
    ps.save()
    reset = reset_life_status(equity=2000, cash=2000)
    assert reset.life_status == "alive"
    assert reset.kill_switch is False


@pytest.mark.django_db
def test_live_mark_taken_locked_before_gates(client):
    from rest_framework.test import APIClient
    from django.utils import timezone
    from datetime import timedelta

    inst = Instrument.objects.create(symbol="PAPERLOCK.NS")
    sig = Signal.objects.create(
        instrument=inst,
        side="BUY",
        data_as_of=timezone.now(),
        data_source="yfinance",
        valid_until=timezone.now() + timedelta(days=1),
        entry_trigger=50,
        entry_limit=50.15,
        sl_trigger=48,
        sl_limit=47.85,
        target1=54,
        target2=56,
        qty=1,
        capital_used=50,
        max_loss=2,
        rr_net=2.0,
        probability=0.7,
        chase_limit=50.5,
        status=Signal.Status.NEW,
    )
    api = APIClient()
    resp = api.post(f"/api/signals/{sig.id}/mark-taken/", {"mode": "live"}, format="json")
    assert resp.status_code == 200
    # Forced to paper when gates not passed
    assert resp.data["mode"] == "paper"
    sig.refresh_from_db()
    assert sig.mode == "paper"


@pytest.mark.django_db
def test_health_includes_life_fields():
    from rest_framework.test import APIClient

    api = APIClient()
    resp = api.get("/api/health/")
    assert resp.status_code == 200
    assert "life_status" in resp.data
    assert "dead_floor" in resp.data
    assert resp.data["dead_floor"] == cfg.DEAD_FLOOR
