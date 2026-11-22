"""Live IST session stage + Today expired badges."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.utils import timezone

from marketdata.models import Instrument
from portfolio.models import PortfolioSettings
from signals.models import Signal
from signals.session_stage import (
    CLOSED,
    ENTER,
    NO_NEW_ENTRY,
    PRE_OPEN,
    SQUARE_OFF,
    apply_live_entry_status,
    ist_session_stage,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize(
    "hh,mm,expected",
    [
        (9, 0, PRE_OPEN),
        (10, 0, ENTER),
        (13, 59, ENTER),
        (14, 1, NO_NEW_ENTRY),
        (15, 11, SQUARE_OFF),
        (16, 0, CLOSED),
    ],
)
def test_ist_session_stage_clock(hh, mm, expected):
    now = datetime(2026, 11, 11, hh, mm, tzinfo=IST)  # Wednesday
    info = ist_session_stage(now)
    assert info["stage"] == expected
    if expected in {NO_NEW_ENTRY, SQUARE_OFF, CLOSED}:
        assert info["force_expired"] is True
        assert info["fno_actionable"] is False
        assert info["status_hint"] == "expired"
        assert "14:00" in info["wait_range_fno"] or "closed" in info["wait_range_fno"].lower() or "next session" in info["wait_range_fno"].lower() or "square-off" in info["wait_range_fno"].lower()
    if expected == ENTER:
        assert info["enter_ok"] is True
        assert info["fno_actionable"] is True


def test_apply_live_entry_status_forces_expired_after_cutoff():
    stage = ist_session_stage(datetime(2026, 11, 11, 14, 5, tzinfo=IST))
    assert apply_live_entry_status(stored="waiting", strategy="fno_intraday", stage_info=stage) == "expired"
    assert apply_live_entry_status(stored="triggered", strategy="fno_intraday", stage_info=stage) == "expired"


@pytest.mark.django_db
def test_signals_partial_shows_expired_after_1400(db):
    from django.urls import reverse

    ps = PortfolioSettings.get_solo()
    ps.cash = Decimal("2000")
    ps.equity = Decimal("2000")
    ps.fno_watchlist = ["NIFTY"]
    ps.save()
    inst = Instrument.objects.create(symbol="NIFTY")
    Signal.objects.create(
        instrument=inst,
        side="BUY",
        strategy="fno_intraday",
        product="NRML",
        data_as_of=timezone.now(),
        data_source="t",
        valid_until=timezone.now(),
        entry_trigger=12,
        entry_limit=12,
        sl_trigger=8,
        sl_limit=8,
        target1=20,
        target2=20,
        qty=1,
        capital_used=800,
        max_loss=300,
        rr_net=1.5,
        probability=0.55,
        chase_limit=15,
        status=Signal.Status.SKIPPED,
        entry_status=Signal.EntryStatus.WAITING,
        gate={"result": "BLOCK", "blocked_by": ["watch — test"]},
        reasons={
            "wait_range": "11-Nov-2026 09:46–14:00 IST",
            "hold_until": "11-Nov-2026 15:10 IST same day only",
            "fno": {
                "action": "BUY CALL",
                "underlying": "NIFTY",
                "option_type": "CE",
                "strike": 26600,
                "lot_size": 65,
                "buy_ltp": 12,
                "pricing": "LIVE_INDSTOCKS",
                "live": {"live_ltp": 12, "pricing": "LIVE_INDSTOCKS"},
                "net_edge_ok": True,
                "affordable": True,
                "max_premium_per_share": 15,
                "max_premium_budget_rs": 985,
                "buy_debit_rs": 780,
                "sl_ltp": 8,
                "target_ltp": 19,
                "loss_at_sl_rs": 260,
                "profit_at_tgt_rs": 400,
                "charges_round_trip_rs": 50,
                "expiry_label": "17-Nov-2026",
                "enter_after_ist": "09:46",
                "square_off_by_ist": "15:10",
                "exit_rules": ["test"],
            },
        },
        time_machine={"verdict": "UNKNOWN"},
    )
    frozen = datetime(2026, 11, 11, 15, 20, tzinfo=IST)
    client = Client()
    with mock.patch(
        "signals.session_stage.ist_session_stage",
        return_value=ist_session_stage(frozen),
    ):
        resp = client.get(reverse("signals_partial"), {"tab": "all"})
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "SQUARE_OFF" in body
    assert "EXPIRED" in body
    assert "Expired today" in body
    # Must not present frozen morning window as still open
    assert "do not buy" in body.lower() or "square-off" in body.lower() or "exit f&o" in body.lower()
    sig = Signal.objects.get(instrument=inst)
    assert sig.entry_status == Signal.EntryStatus.EXPIRED
