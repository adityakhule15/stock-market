"""F&O pipeline must keep premium SL/target — never underlying ATR on premiums."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from dashboard.ranking import _is_coin_flip, rank_signals
from portfolio.management import suggest_management
from signals.fno import sane_option_target
from signals.pipeline import run_pipeline


def _pf():
    return SimpleNamespace(
        free_cash=2000.0,
        equity=2000.0,
        risk_per_trade=0.015,
        max_position_pct=0.4,
        life_status="alive",
        kill_switch=False,
        open_positions=0,
        trades_today=0,
        max_trades_per_day=3,
        max_open_positions=2,
        day_pnl=0.0,
        week_pnl=0.0,
        daily_realized=0.0,
        weekly_realized=0.0,
        revenge_symbols=set(),
        lockout_until=None,
        avg_volumes={},
        min_prob=0.45,
        avg_volume=lambda _sym: 10_000_000,
    )


@pytest.fixture
def nifty_bars():
    # Underlying ~26000 — must not be mixed into premium levels
    idx = pd.date_range("2026-01-01", periods=80, freq="B")
    close = pd.Series(26000 + (pd.Series(range(80)) % 7) * 20, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close - 10,
            "high": close + 40,
            "low": close - 40,
            "close": close,
            "volume": 1_000_000,
        },
        index=idx,
    )


def test_sane_option_target_repairs_index_atr_corruption():
    assert sane_option_target(5.65, 2177.15) == pytest.approx(9.04)
    assert sane_option_target(5.65, 9.04) == pytest.approx(9.04)
    m = suggest_management(
        entry=5.65, stop=4.52, target=2177.15, mark=7.40, side="BUY", atr=200.0, is_option=True
    )
    assert float(m["suggested_target"]) < 20
    assert float(m["suggested_target"]) > 5.65


@pytest.mark.django_db
def test_practice_invested_tracks_lots_and_edit():
    """Invested = entry×units even when reserved_cash is 0 (practice); edit must update it."""
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from marketdata.models import Instrument
    from portfolio.live import enrich_position, position_notional
    from portfolio.models import Position
    from portfolio.services import edit_position
    from signals.models import Signal

    inst = Instrument.objects.create(symbol="BANKNIFTY")
    sig = Signal.objects.create(
        instrument=inst,
        side="BUY",
        strategy="fno_intraday",
        product="NRML",
        data_as_of=timezone.now(),
        data_source="t",
        valid_until=timezone.now() + timedelta(days=1),
        entry_trigger=14.90,
        entry_limit=14.90,
        sl_trigger=11.92,
        sl_limit=11.92,
        target1=23.84,
        target2=23.84,
        qty=1,
        capital_used=447,
        max_loss=90,
        rr_net=1.5,
        probability=0.5,
        chase_limit=16,
        status="triggered",
        reasons={"fno": {"underlying": "BANKNIFTY", "option_type": "CE", "strike": 65000, "lot_size": 30}},
    )
    pos = Position.objects.create(
        instrument=inst,
        signal=sig,
        side="BUY",
        qty=1,
        entry_price=Decimal("14.90"),
        stop=Decimal("11.92"),
        target=Decimal("23.84"),
        status=Position.Status.OPEN,
        is_dummy=True,
        reserved_cash=Decimal("0"),
        notes="practice track (wallet unchanged)",
    )
    assert float(position_notional(pos)) == pytest.approx(447.0)
    row = enrich_position(pos)
    assert row["invested"] == pytest.approx(447.0)

    edit_position(pos, qty=2, entry_price=14.90, stop=11.92, target=23.84)
    pos.refresh_from_db()
    assert int(pos.qty) == 2
    assert float(position_notional(pos)) == pytest.approx(894.0)
    row2 = enrich_position(pos)
    assert row2["invested"] == pytest.approx(894.0)
    assert row2["qty_label"].startswith("2 lot")

    # Same invested figure after flipping to ORIGINAL (wallet locks; amount still updates)
    from portfolio.models import PortfolioSettings
    from portfolio.services import set_position_dummy

    ps = PortfolioSettings.get_solo()
    cash_before = float(ps.cash)
    set_position_dummy(pos, False)
    pos.refresh_from_db()
    ps.refresh_from_db()
    assert pos.is_dummy is False
    assert float(pos.reserved_cash) > 0
    assert float(ps.cash) < cash_before
    row3 = enrich_position(pos)
    assert row3["invested"] == pytest.approx(894.0)

    edit_position(pos, qty=1, entry_price=14.90, stop=11.92, target=23.84)
    pos.refresh_from_db()
    row4 = enrich_position(pos)
    assert row4["invested"] == pytest.approx(447.0)
    assert float(pos.reserved_cash) > 0


def test_fno_pipeline_keeps_premium_levels(nifty_bars):
    order = {
        "side": "BUY",
        "entry_trigger": 12.0,
        "entry_limit": 12.0,
        "sl_trigger": 7.8,
        "sl_limit": 7.8,
        "target1": 19.2,
        "target2": 19.2,
        "chase_limit": 15.0,
        "qty": 1,
        "capital_used": 780.0,
        "max_loss": 273.0,
        "rr_net": 1.5,
        "probability": 0.55,
        "product": "NRML",
        "strategy": "fno_intraday",
        "atr": 180.0,  # underlying ATR — must NOT rewrite SL
    }
    out = run_pipeline(
        symbol="NIFTY",
        scanner={"ok": True, "setup": "fno_debit", "reason": "test", "hits": []},
        order=order,
        pf=_pf(),
        tm_bars=nifty_bars,
        probability=0.55,
        is_option=True,
        lot_size=65,
        lots=1,
        option_type="CE",
        strike=26500,
    )
    o = out["order"]
    assert float(o["sl_trigger"]) == pytest.approx(7.8)
    assert float(o["target1"]) == pytest.approx(19.2)
    assert float(o["sl_trigger"]) > 0
    tm = out["time_machine"]
    assert tm.get("verdict") != "COIN_FLIP"
    assert tm.get("mode") == "fno_local"
    assert "COIN FLIP" not in " ".join(out["gate"].reasons)


def test_rank_keeps_fno_despite_legacy_coin_flip_tm():
    """Old tickets with bogus COIN_FLIP from underlying TM should still rank."""
    sig = SimpleNamespace(
        id=1,
        strategy="fno_intraday",
        ticket_kind="fno",
        capital_used=600,
        max_loss=200,
        probability=0.54,
        rr_net=1.5,
        entry_trigger=9.15,
        target1=14.64,
        side="BUY",
        qty=1,
        product="NRML",
        instrument=SimpleNamespace(symbol="NIFTY"),
        time_machine={"verdict": "COIN_FLIP", "skip_recommended": True},
        pipeline={"time_machine": {"verdict": "COIN_FLIP", "skip_recommended": True}},
        gate={"result": "BLOCK"},
        reasons={
            "fno": {
                "pricing": "LIVE_INDSTOCKS",
                "buy_ltp": 9.15,
                "target_ltp": 14.64,
                "lot_size": 65,
                "underlying": "NIFTY",
                "option_type": "CE",
                "strike": 26500,
                "expiry_label": "17-Nov-2026",
                "net_edge_ok": True,
                "economics": {
                    "verdict": "NET_OK",
                    "net_ok": True,
                    "net_pnl": 300.0,
                    "gross_pnl": 350.0,
                    "charges_total": 50.0,
                    "tip": "ok",
                    "breakeven_sell": 10.0,
                },
            }
        },
        groww={},
    )
    assert _is_coin_flip(sig) is False
    ranked = rank_signals([sig], wallet=1755.0, limit=3)
    assert len(ranked) == 1
