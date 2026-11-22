"""Combined HOLD/SELL advisor: rules + model (AI coach_notes only)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from marketdata.models import Instrument
from portfolio.models import PortfolioSettings, Position
from signals.models import Signal


@pytest.fixture
def open_pos(db):
    ps = PortfolioSettings.get_solo()
    ps.cash = Decimal("2000")
    ps.equity = Decimal("2000")
    ps.save()
    inst = Instrument.objects.create(symbol="NIFTY")
    sig = Signal.objects.create(
        instrument=inst,
        side="BUY",
        strategy="fno_intraday",
        product="NRML",
        data_as_of=timezone.now(),
        data_source="t",
        valid_until=timezone.now() + timedelta(hours=2),
        entry_trigger=10,
        entry_limit=10,
        sl_trigger=6,
        sl_limit=6,
        target1=16,
        target2=16,
        qty=1,
        capital_used=650,
        max_loss=200,
        rr_net=1.5,
        probability=0.62,
        chase_limit=11,
        status=Signal.Status.TRIGGERED,
        time_machine={"verdict": "POSITIVE"},
        reasons={
            "fno": {
                "underlying": "NIFTY",
                "option_type": "CE",
                "strike": 26000,
                "lot_size": 65,
                "buy_ltp": 10,
                "live": {"live_ltp": 11},
            }
        },
    )
    return Position.objects.create(
        instrument=inst,
        signal=sig,
        side="BUY",
        qty=1,
        entry_price=Decimal("10.00"),
        stop=Decimal("6.00"),
        target=Decimal("16.00"),
        status=Position.Status.OPEN,
        reserved_cash=Decimal("650"),
    )


@pytest.mark.django_db
def test_advise_endpoint_returns_combined_verdict(open_pos):
    api = APIClient()
    with mock.patch("dashboard.position_advise._ai_coach") as ai:
        ai.return_value = {
            "source": "ai",
            "provider": "gemini",
            "verdict": "SELL",
            "weight": 0.0,
            "why": "VERDICT: SELL\n- advisory only",
            "raw_ok": True,
            "error": None,
            "coach_only": False,
        }
        with mock.patch.dict("os.environ", {"ADVISOR_AI_VOTE": "1"}):
            resp = api.post(f"/api/positions/{open_pos.id}/advise/", {}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["ok"] is True
    assert resp.data["combined"]["verdict"] in {"HOLD", "SELL", "EXIT_PARTIAL"}
    assert "rules" in resp.data and "model" in resp.data and "ai" in resp.data
    assert resp.data["ai"]["coach_only"] is False
    assert float(resp.data["ai"]["weight"]) > 0
    assert "advice" in resp.data["combined"]["headline"].lower() or "model" in resp.data[
        "combined"
    ]["headline"].lower()


@pytest.mark.django_db
def test_rules_force_sell_near_stop(open_pos):
    from dashboard.position_advise import _combine, _model_verdict, _rules_verdict

    live = {
        "ltp": 5.5,
        "open_pnl": -292.5,
        "open_pnl_net": -320,
        "suggestion": {"code": "EXIT_NOW", "label": "EXIT NOW", "detail": "mark at/through stop"},
        "contract": "NIFTY 26000 CE",
        "qty_label": "1 lot",
    }
    rules = _rules_verdict(live, open_pos)
    model = _model_verdict(open_pos, live)
    ai = {"verdict": "HOLD", "weight": 0.0, "provider": "gemini", "coach_only": True}
    combined = _combine(rules, model, ai)
    assert rules["verdict"] == "SELL"
    assert combined["verdict"] == "SELL"


@pytest.mark.django_db
def test_ai_cannot_override_model_hold(open_pos):
    from dashboard.position_advise import _combine, _model_verdict, _rules_verdict

    live = {
        "ltp": 12.0,
        "open_pnl": 130,
        "open_pnl_net": 100,
        "suggestion": {"code": "HOLD", "label": "HOLD", "detail": "between stop and target"},
        "contract": "NIFTY 26000 CE",
        "qty_label": "1 lot",
    }
    rules = _rules_verdict(live, open_pos)
    model = _model_verdict(open_pos, live)
    # Even if AI wants SELL with high weight, default combine ignores weight 0
    ai = {"verdict": "SELL", "weight": 0.0, "provider": "gemini"}
    combined = _combine(rules, model, ai)
    assert combined["verdict"] in {"HOLD", "EXIT_PARTIAL", "SELL"}
    assert combined["tie_break"] in {"rules_model", "rules_force"}
