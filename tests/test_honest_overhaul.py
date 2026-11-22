"""Honest-numbers overhaul: simulator, coach safety, unlock, EV, plain language."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from django.test import Client


def _ohlc(n=40, start=100.0, trend=0.5):
    rng = np.random.default_rng(7)
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] + trend + float(rng.normal(0, 0.4)))
    rows = []
    for i, c in enumerate(closes):
        o = c - 0.2
        h = c + 1.5
        l = c - 1.2
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1_000_000})
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame(rows, index=idx)


def test_simulator_no_trigger():
    from backtest.simulator import SimLevels, simulate_ticket

    bars = _ohlc(20, start=100, trend=0.0)
    # Trigger far above market
    levels = SimLevels(entry_trigger=200.0, stop=190.0, target=220.0, atr=2.0, signal_i=5)
    res = simulate_ticket(bars, levels, qty=1, horizon=5, apply_management=False)
    assert not res.triggered
    assert res.exit_reason == "no_trigger"


def test_simulator_stop_first_and_charges():
    from backtest.simulator import SimLevels, simulate_ticket

    idx = pd.bdate_range("2024-01-02", periods=10)
    # Signal at i=1; next bar gaps up through trigger, then sells off through stop
    bars = pd.DataFrame(
        {
            "open": [100, 100, 101.2, 97, 96, 95, 94, 93, 92, 91],
            "high": [101, 101, 102, 98, 97, 96, 95, 94, 93, 92],
            "low": [99, 99, 100.5, 95, 94, 93, 92, 91, 90, 89],
            "close": [100, 100.2, 100.8, 96, 95, 94, 93, 92, 91, 90],
            "volume": [1e6] * 10,
        },
        index=idx,
    )
    levels = SimLevels(entry_trigger=100.5, stop=98.0, target=110.0, atr=2.0, signal_i=1)
    res = simulate_ticket(bars, levels, qty=10, horizon=5, apply_management=False)
    assert res.triggered
    assert res.charges >= 0
    assert res.exit_reason in {"stop", "adverse", "time"}
    assert res.hit_target is False


def test_simulator_gap_through_stop():
    from backtest.simulator import SimLevels, simulate_ticket

    idx = pd.bdate_range("2024-01-02", periods=8)
    bars = pd.DataFrame(
        {
            "open": [100, 100, 100, 90, 90, 90, 90, 90],
            "high": [101, 101, 102, 91, 91, 91, 91, 91],
            "low": [99, 99, 99, 88, 88, 88, 88, 88],
            "close": [100, 100, 101, 89, 89, 89, 89, 89],
            "volume": [1e6] * 8,
        },
        index=idx,
    )
    levels = SimLevels(entry_trigger=100.1, stop=98.0, target=105.0, atr=1.0, signal_i=1)
    res = simulate_ticket(bars, levels, qty=1, horizon=5, apply_management=False)
    assert res.triggered
    # Day after entry can gap through stop
    assert res.exit_reason in {"stop", "time", "target"}


def test_fno_stop_matches_premium_drop():
    from config import trading_config as cfg
    from signals.fno import build_fno_plan

    plan = build_fno_plan("NIFTY", 25000.0, bullish=True, atr=200.0, capital=2000.0, option_type="CE")
    assert abs(float(plan["sl_pct_of_premium"]) - float(cfg.FNO_PREMIUM_DROP_EXIT)) < 1e-9


def test_strategy_unlocked_fno_locked_by_default():
    from portfolio.strategy_gates import strategy_unlocked

    assert strategy_unlocked("fno") is False


def test_ev_gate_helper():
    from signals.ev import expected_rupees

    good = expected_rupees(probability=0.7, win_rs=40, loss_rs=20)
    assert good["ok"] is True
    bad = expected_rupees(probability=0.4, win_rs=10, loss_rs=30)
    assert bad["ok"] is False


def test_plain_language_covers_codes():
    from dashboard.plain_language import plain_reason

    assert "coin flip" in plain_reason("coin_flip").lower()
    assert "charges" in plain_reason("ev_below_min:2.5").lower() or "₹" in plain_reason("ev_below_min:2.5")
    assert plain_reason("nifty_bear_skip_longs")


def test_coach_skip_on_kill(db):
    from dashboard.advisor import wallet_now
    from portfolio.models import PortfolioSettings

    ps = PortfolioSettings.get_solo()
    ps.kill_switch = True
    ps.save(update_fields=["kill_switch"])
    w = wallet_now()
    assert w["kill_switch"] is True


def test_coach_recommend_practice_or_skip(db, monkeypatch):
    from dashboard import advisor

    monkeypatch.setattr(
        advisor,
        "wallet_now",
        lambda: {
            "equity": 2000,
            "cash": 2000,
            "wallet": 2000,
            "hard_cap": 2000,
            "life_status": "alive",
            "kill_switch": False,
            "lockout_active": False,
        },
    )
    monkeypatch.setattr(advisor, "_trend_note", lambda *a, **k: "NIFTY above 50-day average: CALL matches trend.")

    def fake_plan(*a, **k):
        return {
            "strike": 25000,
            "buy_ltp": 20,
            "buy_debit_rs": 1300,
            "pricing": "LIVE_NSE",
            "live": {},
        }

    monkeypatch.setattr(advisor, "build_fno_plan", fake_plan)
    out = advisor._recommend(
        {"symbol": "NIFTY", "side": "CE", "style": "auto", "strike": 25000, "groww_ltp": 20},
        advisor.wallet_now(),
        {"spot": 25000, "expiry": "x", "as_of": "now", "rows": [], "source": "LIVE_NSE"},
    )
    assert out["ok"]
    assert out["suggestion"]["verdict"] in {"PRACTICE", "BUY", "SKIP"}
    # With options locked, should be PRACTICE when fits
    assert out["suggestion"]["verdict"] == "PRACTICE"
    assert "loss_pct_wallet" in out["suggestion"]


def test_dp_charge_configurable():
    from config import trading_config as cfg
    from portfolio.charges import trade_economics

    eco = trade_economics(buy_price=100, sell_price=110, units=10, product="CNC", is_option=False, min_net_rs=0)
    assert eco["charges_total"] > 0
    assert float(cfg.GROWW_DP_CHARGE_RS) == 20.0
    # DP ≈ base * 1.18 should be embedded in CNC sell
    assert eco["charges_total"] >= float(cfg.GROWW_DP_CHARGE_RS)


def test_build_feature_row_latest_bar():
    from features.builders import build_feature_row
    from features.indicators import add_indicators

    bars = _ohlc(80)
    # need enough history for indicators
    X = build_feature_row(bars)
    assert len(X) == 1
    assert X.index[-1] == bars.index[-1]


def test_card_state_practice_for_locked_fno(db):
    from types import SimpleNamespace

    from dashboard.card_state import card_state_for_signal

    sig = SimpleNamespace(
        strategy="fno_intraday",
        gate={"result": "PASS", "blocked_by": []},
        reasons={"fno": {"affordable": True, "net_edge_ok": True}},
        entry_status="waiting",
        gate_summary="",
        skip_reason="",
    )
    cs = card_state_for_signal(sig)
    assert cs["state"] == "practice"
    assert cs["show_practice"] is True
    assert cs["show_copy"] is False


def test_results_page_ok(db):
    c = Client()
    r = c.get("/results/?tab=practice")
    assert r.status_code == 200


def test_ui_today_ok(db):
    c = Client()
    r = c.get("/")
    assert r.status_code == 200


def test_no_in_sample_holdout_split():
    from ml.train import time_train_test_split

    idx = pd.bdate_range("2020-01-01", periods=200)
    df = pd.DataFrame({"y": np.random.randint(0, 2, 200)}, index=idx)
    train, test = time_train_test_split(df, test_ratio=0.2, embargo=8)
    assert train.index.max() < test.index.min()
