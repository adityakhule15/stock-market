"""Close without TradeReview must fail via API."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from marketdata.models import Instrument
from signals.models import Signal


@pytest.mark.django_db
def test_close_without_review_fails():
    inst, _ = Instrument.objects.get_or_create(symbol="REVREQ.NS")
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
    )
    api = APIClient()
    taken = api.post(
        f"/api/signals/{sig.id}/mark-taken/",
        {"order_confirmed": True, "fill_price": 100, "mode": "paper"},
        format="json",
    )
    assert taken.status_code == 200
    pid = taken.data["position_id"]
    bad = api.post(
        f"/api/positions/{pid}/close/",
        {"exit_price": 105, "reason": "manual"},
        format="json",
    )
    assert bad.status_code == 400
    assert "review" in str(bad.data.get("error", "")).lower() or "mistake" in str(bad.data).lower()

    ok = api.post(
        f"/api/positions/{pid}/close/",
        {
            "exit_price": 105,
            "exit_reason": "target",
            "mistake_tags": ["followed_signal_correctly"],
        },
        format="json",
    )
    assert ok.status_code == 200
    assert ok.data.get("review")
