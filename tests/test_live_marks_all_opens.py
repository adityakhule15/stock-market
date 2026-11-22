"""Every open F&O order must get a display LTP (live, nearest, or fallback)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from marketdata.models import Instrument
from portfolio.live import _ltp_from_chain, enrich_position, fetch_mark_price
from portfolio.models import Position
from signals.models import Signal
from django.utils import timezone
from datetime import timedelta


@pytest.mark.django_db
def test_deep_otm_strike_uses_nearest_or_fallback():
    chain = {
        "source": "LIVE_INDSTOCKS",
        "as_of_label": "now",
        "spot": 25200,
        "expiry": "17-Nov-2026",
        "rows": [
            {"strike": 26400.0, "ce_ltp": 14.7, "pe_ltp": 1.0},
            {"strike": 26450.0, "ce_ltp": 13.45, "pe_ltp": 1.0},
        ],
    }
    # Exact miss — nearest within 150
    ltp, info = _ltp_from_chain(chain, 26500.0, "CE", max_dist=150.0)
    assert ltp == 13.45
    assert info.get("approx_strike") is True


@pytest.mark.django_db
def test_enrich_never_blanks_open_fno_when_meta_has_live():
    inst = Instrument.objects.create(symbol="NIFTY")
    sig = Signal.objects.create(
        instrument=inst,
        side="BUY",
        strategy="fno_intraday",
        product="NRML",
        data_as_of=timezone.now(),
        data_source="t",
        valid_until=timezone.now() + timedelta(days=1),
        entry_trigger=15.25,
        entry_limit=15.25,
        sl_trigger=10,
        sl_limit=10,
        target1=20,
        target2=20,
        qty=1,
        capital_used=1000,
        max_loss=300,
        rr_net=1.5,
        probability=0.5,
        chase_limit=16,
        status="triggered",
        reasons={
            "fno": {
                "underlying": "NIFTY",
                "option_type": "CE",
                "strike": 29999.0,  # absurd — won't be on chain
                "lot_size": 65,
                "buy_ltp": 15.25,
                "live": {"live_ltp": 14.5, "chain_as_of": "test"},
            }
        },
    )
    pos = Position.objects.create(
        instrument=inst,
        signal=sig,
        side="BUY",
        qty=1,
        entry_price=Decimal("15.25"),
        stop=Decimal("10"),
        target=Decimal("20"),
        status=Position.Status.OPEN,
        reserved_cash=Decimal("1000"),
    )
    # Empty chain forces fallback path
    mark = fetch_mark_price(pos, chains={"NIFTY": {"rows": [], "source": "empty"}})
    assert mark.get("ok") is True
    assert mark.get("ltp") == 14.5
    row = enrich_position(pos, chains={"NIFTY": {"rows": [], "source": "empty"}})
    assert row.get("ltp") == 14.5
    assert row.get("current_value") is not None
    assert row.get("open_pnl") is not None
