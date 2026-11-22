"""Wallet floors, cooling-off lockout, revenge block, floor-breach sizing."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from config import trading_config as cfg
from marketdata.models import Instrument
from portfolio.models import PortfolioSettings, Position
from portfolio.services import close_position, get_portfolio_state, mark_signal_taken
from signals.budget import budget_stage
from signals.gate import PortfolioState, risk_gate
from signals.models import Signal


@pytest.mark.django_db
def test_cooling_off_after_two_losses():
    inst, _ = Instrument.objects.get_or_create(symbol="TESTLOCK.NS")
    ps = PortfolioSettings.get_solo()
    ps.equity = Decimal("2000")
    ps.cash = Decimal("2000")
    ps.consecutive_losses = 0
    ps.lockout_until = None
    ps.save()

    def _open_and_lose():
        sig = Signal.objects.create(
            instrument=inst,
            side="BUY",
            strategy="swing",
            product="CNC",
            data_as_of=timezone.now(),
            data_source="test",
            valid_until=timezone.now() + timedelta(days=1),
            entry_trigger=100,
            entry_limit=100.3,
            sl_trigger=95,
            sl_limit=94.7,
            target1=110,
            target2=115,
            qty=1,
            capital_used=100,
            max_loss=5,
            rr_net=2.0,
            probability=0.6,
            chase_limit=101,
            gate={"result": "PASS"},
            time_machine={"verdict": "EDGE"},
        )
        pos = mark_signal_taken(sig, fill_price=100, qty=1)
        close_position(
            pos,
            exit_price=90,
            exit_reason="sl",
            mistake_tags=["ignored_sl"],
            require_review=True,
        )

    _open_and_lose()
    ps = PortfolioSettings.get_solo()
    assert ps.consecutive_losses == 1
    assert ps.lockout_until is None
    _open_and_lose()
    ps.refresh_from_db()
    assert ps.consecutive_losses >= 2
    assert ps.lockout_until is not None
    assert ps.lockout_until > timezone.now()

    pf = get_portfolio_state()
    order = {
        "symbol": "OTHER.NS",
        "max_loss": 10,
        "capital_used": 100,
        "rr_net": 2.0,
        "qty": 1,
        "probability": 0.7,
        "strategy": "swing",
        "product": "CNC",
    }
    gate = risk_gate(order, pf, {"verdict": "EDGE"}, data_age_min=0)
    assert gate.passed is False
    assert any("lockout" in r.lower() or "cooling" in r.lower() for r in gate.reasons)


@pytest.mark.django_db
def test_revenge_block_same_symbol_after_sl():
    inst, _ = Instrument.objects.get_or_create(symbol="REVENGE.NS")
    Position.objects.create(
        instrument=inst,
        side="BUY",
        qty=1,
        entry_price=100,
        stop=95,
        target=110,
        status=Position.Status.CLOSED,
        exit_price=94,
        pnl=Decimal("-6"),
        notes="sl hit",
        closed_at=timezone.now(),
    )
    pf = get_portfolio_state()
    assert "REVENGE" in pf.revenge_symbols
    order = {
        "symbol": "REVENGE.NS",
        "max_loss": 10,
        "capital_used": 100,
        "rr_net": 2.0,
        "qty": 1,
        "probability": 0.7,
        "strategy": "swing",
        "product": "CNC",
    }
    gate = risk_gate(order, pf, {"verdict": "EDGE"}, data_age_min=0)
    assert any("revenge" in r.lower() for r in gate.reasons)


def test_budget_blocks_floor_breach():
    # Equity just above SURVIVAL — loss must not push under 1700
    out = budget_stage(
        entry=100,
        stop=50,
        target=150,
        capital=1750,
        equity=1750,
        risk_pct=0.5,
        max_position_pct=1.0,
    )
    # Huge stop distance → either qty 0 or floor block
    assert out["ok"] is False or out.get("rupees_at_risk", 0) == 0 or "floor" in out.get("reason", "").lower()


def test_survival_top_n_config():
    assert cfg.SURVIVAL_TOP_N == 1
    assert cfg.ALIVE_TOP_N == 3
    assert cfg.DAILY_LOSS_CAP == 0.03
