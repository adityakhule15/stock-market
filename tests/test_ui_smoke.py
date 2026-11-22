"""UI smoke tests — pages we edit must stay clickable and renderable.

Catches: template VariableDoesNotExist, broken scan/tabs markup, positions controls,
reanalyze float(dict), and scan mode=all.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from marketdata.models import Instrument
from portfolio.models import PortfolioSettings, Position
from signals.models import Signal


def _mk_signal(**kwargs):
    defaults = dict(
        side="BUY",
        strategy="swing",
        product="CNC",
        data_as_of=timezone.now(),
        data_source="test",
        valid_until=timezone.now() + timedelta(days=1),
        entry_trigger=100,
        entry_limit=100.5,
        sl_trigger=95,
        sl_limit=94.5,
        target1=110,
        target2=115,
        qty=1,
        capital_used=100,
        max_loss=5,
        rr_net=2.0,
        probability=0.55,
        chase_limit=101,
        status=Signal.Status.NEW,
        gate={},
        reasons={},
        time_machine={},
    )
    defaults.update(kwargs)
    return Signal.objects.create(**defaults)


@pytest.fixture
def ps():
    obj = PortfolioSettings.get_solo()
    obj.cash = Decimal("2000")
    obj.equity = Decimal("2000")
    obj.life_status = PortfolioSettings.LifeStatus.ALIVE
    obj.kill_switch = False
    obj.fno_watchlist = ["NIFTY", "BANKNIFTY"]
    obj.save()
    return obj


@pytest.fixture
def equity_signal(ps):
    inst, _ = Instrument.objects.get_or_create(symbol="TESTCO.NS", defaults={"name": "Test"})
    return _mk_signal(
        instrument=inst,
        gate={"result": "BLOCK", "blocked_by": ["watch — below min prob/RR"]},
        # No "setup" key — previously crashed template via |default:s.reasons.setup
        reasons={
            "gates": ["watch — below min prob/RR"],
            "prob": 0.05,
            "kind": "normal",
            "hold": "SWING_MULTI_DAY_OK",
            "wallet": 2000.0,
            "ref_price": 100.0,
        },
        time_machine={"verdict": "NEGATIVE"},
    )


@pytest.mark.django_db
def test_today_page_has_clickable_scan_controls(client, ps):
    resp = client.get(reverse("signals"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'id="btn-scan"' in html
    assert 'id="board-tabs"' in html
    assert 'data-tab="all"' in html
    assert 'data-tab="normal"' in html
    assert 'data-tab="fno"' in html
    assert 'id="signals"' in html
    assert "today.js" in html or "Scan now" in html


@pytest.mark.django_db
@pytest.mark.parametrize("tab", ["all", "normal", "fno"])
def test_signals_partial_never_500(client, ps, equity_signal, tab):
    resp = client.get(reverse("signals_partial"), {"tab": tab})
    assert resp.status_code == 200, resp.content[:500]
    html = resp.content.decode()
    # Must not be a Django debug traceback page
    assert "VariableDoesNotExist" not in html
    assert "Traceback" not in html or "Signals failed to render" in html


@pytest.mark.django_db
def test_signal_card_renders_without_reasons_setup(client, ps, equity_signal):
    resp = client.get(reverse("signals_partial"), {"tab": "normal"})
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "VariableDoesNotExist" not in html
    # Card or empty state is fine — just no crash
    assert "Scan" in html or "sig-" in html or "No tickets" in html or "idea" in html or "Sit out" in html or "Ready" in html


@pytest.mark.django_db
def test_positions_page_has_edit_and_add_controls(client, ps):
    resp = client.get(reverse("positions"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'id="btn-add-order"' in html
    assert 'id="manual-form"' in html
    assert 'id="edit-form"' in html
    assert 'id="e-lots"' in html
    assert 'id="e-stop"' in html
    assert 'id="e-target"' in html
    assert "pollLive" in html or "startLive" in html


@pytest.mark.django_db
def test_scan_mode_all_accepted(ps):
    api = APIClient()
    # May create 0 tickets offline; must not 500
    resp = api.post("/api/signals/scan/", {"mode": "all"}, format="json")
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        assert resp.data.get("ok") is True
        assert resp.data.get("mode") == "all"
        assert "created_normal" in resp.data
        assert "created_fno" in resp.data


@pytest.mark.django_db
def test_reanalyze_handles_missing_ltp_mark_dict(ps):
    """Regression: live['mark'] is a dict — must not float(mark)."""
    inst, _ = Instrument.objects.get_or_create(symbol="NIFTY", defaults={"name": "Nifty"})
    pos = Position.objects.create(
        instrument=inst,
        side="BUY",
        qty=1,
        entry_price=Decimal("15.00"),
        stop=Decimal("10.00"),
        target=Decimal("20.00"),
        status=Position.Status.OPEN,
        notes='{"fno":{"underlying":"NIFTY","option_type":"CE","strike":26500,"lot_size":65}} lot_reserved',
        reserved_cash=Decimal("1000"),
    )
    # Store fno meta the way live.fno_meta expects (via signal reasons)
    sig = _mk_signal(
        instrument=inst,
        strategy="fno_intraday",
        product="NRML",
        entry_trigger=15,
        entry_limit=15,
        sl_trigger=10,
        sl_limit=10,
        target1=20,
        target2=20,
        capital_used=1000,
        max_loss=300,
        status=Signal.Status.TRIGGERED,
        reasons={"fno": {"underlying": "NIFTY", "option_type": "CE", "strike": 26500, "lot_size": 65}},
    )
    pos.signal = sig
    pos.save(update_fields=["signal"])

    api = APIClient()
    resp = api.post(f"/api/positions/{pos.id}/reanalyze/", {}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data.get("ok") is True
    assert "live" in resp.data


@pytest.mark.django_db
def test_advisor_and_settings_pages_load(client, ps):
    for name in ("advisor", "settings", "results"):
        resp = client.get(reverse(name))
        assert resp.status_code == 200, name
    # Legacy performance → Results redirect
    resp = client.get(reverse("performance"))
    assert resp.status_code in (200, 301, 302)
