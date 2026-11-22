from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from marketdata.models import Instrument
from portfolio.models import Position
from signals.models import Signal


def _signal():
    inst = Instrument.objects.create(symbol="CONFIRMQ.NS")
    return Signal.objects.create(
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
        qty=2,
        capital_used=200,
        max_loss=6,
        rr_net=2.0,
        probability=0.7,
        chase_limit=100.75,
        status=Signal.Status.NEW,
    )


@pytest.mark.django_db
def test_confirm_yes_with_fill_price():
    sig = _signal()
    api = APIClient()
    resp = api.post(
        f"/api/signals/{sig.id}/mark-taken/",
        {"order_confirmed": True, "fill_price": 101.05, "mode": "paper"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["ok"] is True
    assert resp.data["fill_price"] == 101.05
    assert "analysis" in resp.data
    pos = Position.objects.get(id=resp.data["position_id"])
    assert pos.entry_price == Decimal("101.05")


@pytest.mark.django_db
def test_confirm_no_skips():
    sig = _signal()
    api = APIClient()
    resp = api.post(
        f"/api/signals/{sig.id}/mark-taken/",
        {"order_confirmed": False, "reason": "I did not place this order"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["skipped"] is True
    sig.refresh_from_db()
    assert sig.status == Signal.Status.SKIPPED


@pytest.mark.django_db
def test_close_updates_pnl_analysis():
    sig = _signal()
    api = APIClient()
    taken = api.post(
        f"/api/signals/{sig.id}/mark-taken/",
        {"order_confirmed": True, "fill_price": 100, "mode": "paper"},
        format="json",
    )
    pid = taken.data["position_id"]
    closed = api.post(
        f"/api/positions/{pid}/close/",
        {
            "exit_price": 106,
            "reason": "target",
            "exit_reason": "target",
            "mistake_tags": ["followed_signal_correctly"],
        },
        format="json",
    )
    assert closed.status_code == 200
    # Net of Groww charges (CNC STT/brokerage can exceed a tiny ₹6/share move)
    assert "pnl" in closed.data
    assert closed.data["analysis"]["n_closed"] >= 1
    assert "verdict" in closed.data["analysis"]
    assert closed.data.get("review")
    assert closed.data["review"]["exit_reason"] == "target"
