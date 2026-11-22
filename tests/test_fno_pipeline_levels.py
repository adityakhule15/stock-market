"""F&O pipeline must keep premium SL/target — never underlying ATR on premiums."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from dashboard.ranking import _is_coin_flip, rank_signals
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
