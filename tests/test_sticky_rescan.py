"""Re-scan continuity: update same calls, keep sticky picks, open book always visible."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from dashboard.ranking import mark_sticky_picks, rank_signals
from marketdata.models import Instrument
from portfolio.models import PortfolioSettings, Position
from signals.models import Signal
from signals.tasks import _find_today_open_signal, _save_signal


def _gate(passed=True, reasons=None):
    return SimpleNamespace(passed=passed, reasons=reasons or [], as_dict=lambda: {"result": "PASS" if passed else "BLOCK", "blocked_by": reasons or []})


def _order(**kw):
    base = dict(
        entry_trigger=100,
        entry_limit=100.5,
        sl_trigger=95,
        sl_limit=94.5,
        target1=110,
        target2=115,
        chase_limit=101,
        rr_net=2.0,
        qty=1,
        capital_used=100,
        max_loss=5,
    )
    base.update(kw)
    return base


@pytest.fixture
def inst(db):
    return Instrument.objects.create(symbol="STICKY.NS", name="Sticky Co")


@pytest.mark.django_db
def test_rescan_upserts_same_signal_not_duplicate(inst):
    gate = _gate(True)
    tm = {"verdict": "POSITIVE"}
    first = _save_signal(
        inst=inst,
        order=_order(entry_trigger=100, target1=110),
        side="BUY",
        strategy="swing",
        product="CNC",
        qty=1,
        capital_used=100,
        max_loss=5,
        valid_until=timezone.now() + timedelta(days=1),
        latest_ts=timezone.now(),
        source="test",
        data_age_min=0,
        prob=0.6,
        champ=None,
        tm=tm,
        gate=gate,
        reasons={"kind": "normal", "sticky": True},
        watch_only=False,
    )
    second = _save_signal(
        inst=inst,
        order=_order(entry_trigger=101, target1=112, sl_trigger=96),
        side="BUY",
        strategy="swing",
        product="CNC",
        qty=1,
        capital_used=101,
        max_loss=5,
        valid_until=timezone.now() + timedelta(days=1),
        latest_ts=timezone.now(),
        source="test",
        data_age_min=0,
        prob=0.62,
        champ=None,
        tm=tm,
        gate=gate,
        reasons={"kind": "normal"},
        watch_only=False,
    )
    assert first.id == second.id
    assert Signal.objects.filter(instrument=inst, created_at__date=timezone.localdate()).count() == 1
    second.refresh_from_db()
    assert float(second.entry_trigger) == 101
    assert float(second.target1) == 112
    assert second.reasons.get("sticky") is True  # preserved


@pytest.mark.django_db
def test_rank_keeps_sticky_when_fresh_scores_higher(inst):
    sticky = Signal(
        instrument=inst,
        side="BUY",
        strategy="swing",
        product="CNC",
        data_as_of=timezone.now(),
        data_source="t",
        valid_until=timezone.now() + timedelta(days=1),
        entry_trigger=50,
        entry_limit=50.5,
        sl_trigger=45,
        sl_limit=44.5,
        target1=70,
        target2=75,
        qty=2,
        capital_used=100,
        max_loss=10,
        rr_net=2.5,
        probability=0.55,
        chase_limit=51,
        status=Signal.Status.NEW,
        reasons={
            "sticky": True,
            "kind": "normal",
            "economics": {
                "verdict": "NET_PROFIT",
                "net_ok": True,
                "net_pnl": 30,
                "gross_pnl": 40,
                "charges_total": 10,
                "breakeven_sell": 52,
            },
        },
    )
    sticky.save()
    # Higher raw score but not sticky
    inst2 = Instrument.objects.create(symbol="FRESH.NS")
    fresh = Signal(
        instrument=inst2,
        side="BUY",
        strategy="swing",
        product="CNC",
        data_as_of=timezone.now(),
        data_source="t",
        valid_until=timezone.now() + timedelta(days=1),
        entry_trigger=40,
        entry_limit=40.5,
        sl_trigger=35,
        sl_limit=34.5,
        target1=80,
        target2=90,
        qty=2,
        capital_used=80,
        max_loss=10,
        rr_net=3.0,
        probability=0.8,
        chase_limit=41,
        status=Signal.Status.NEW,
        reasons={
            "kind": "normal",
            "economics": {
                "verdict": "NET_PROFIT",
                "net_ok": True,
                "net_pnl": 60,
                "gross_pnl": 80,
                "charges_total": 20,
                "breakeven_sell": 42,
            },
        },
    )
    fresh.save()
    out = rank_signals([sticky, fresh], wallet=2000, limit=2)
    keys = {s.instrument.symbol for s in out}
    assert "STICKY.NS" in keys  # sticky retained alongside fresh
    assert len(out) == 2


@pytest.mark.django_db
def test_open_positions_passed_to_today_partial(client):
    ps = PortfolioSettings.get_solo()
    ps.cash = Decimal("2000")
    ps.equity = Decimal("2000")
    ps.life_status = "alive"
    ps.save()
    inst = Instrument.objects.create(symbol="OPENME.NS")
    Position.objects.create(
        instrument=inst,
        side="BUY",
        qty=1,
        entry_price=Decimal("10"),
        stop=Decimal("8"),
        target=Decimal("14"),
        status=Position.Status.OPEN,
    )
    resp = client.get("/partials/signals/?tab=all")
    assert resp.status_code == 200
    assert b"Your open calls" in resp.content
    assert b"OPENME.NS" in resp.content


@pytest.mark.django_db
def test_find_today_signal_matches_fno_option_type(inst):
    from signals.tasks import _save_signal as save

    gate = _gate(False, ["watch"])
    a = save(
        inst=inst,
        order=_order(),
        side="BUY",
        strategy="fno_intraday",
        product="NRML",
        qty=1,
        capital_used=500,
        max_loss=100,
        valid_until=timezone.now() + timedelta(hours=2),
        latest_ts=timezone.now(),
        source="t",
        data_age_min=0,
        prob=0.5,
        champ=None,
        tm={},
        gate=gate,
        reasons={"kind": "fno", "fno": {"option_type": "CE", "strike": 100}},
        watch_only=True,
    )
    b = save(
        inst=inst,
        order=_order(entry_trigger=12),
        side="BUY",
        strategy="fno_intraday",
        product="NRML",
        qty=1,
        capital_used=500,
        max_loss=100,
        valid_until=timezone.now() + timedelta(hours=2),
        latest_ts=timezone.now(),
        source="t",
        data_age_min=0,
        prob=0.5,
        champ=None,
        tm={},
        gate=gate,
        reasons={"kind": "fno", "fno": {"option_type": "PE", "strike": 100}},
        watch_only=True,
    )
    assert a.id != b.id  # CE and PE are different calls
    found = _find_today_open_signal(inst, strategy="fno_intraday", side="BUY", option_type="CE")
    assert found and found.id == a.id
