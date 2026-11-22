"""Mocked TradingAgents glue — no live LLM calls."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from research.veto import apply_agent_veto, rating_bucket


@pytest.mark.parametrize(
    "rating,bucket",
    [
        ("Buy", "buy"),
        ("Overweight", "buy"),
        ("Hold", "hold"),
        ("Underweight", "sell"),
        ("Sell", "sell"),
        ("REVIEW", "fail_open"),
        ("", "fail_open"),
    ],
)
def test_rating_bucket(rating, bucket):
    assert rating_bucket(rating) == bucket


def test_veto_cannot_unblock():
    out = apply_agent_veto(gate_passed=False, gate_reasons=["max_loss"], rating="Buy")
    assert out["passed"] is False
    assert out["status"] == "skipped_already_blocked"


def test_veto_sell_blocks_pass():
    out = apply_agent_veto(gate_passed=True, gate_reasons=[], rating="Sell")
    assert out["passed"] is False
    assert "agents_veto" in out["reasons"]
    assert out["agents"]["vetoed"] is True


def test_veto_hold_keeps_pass_neutral():
    out = apply_agent_veto(gate_passed=True, gate_reasons=[], rating="Hold")
    assert out["passed"] is True
    assert out["agents"]["neutral"] is True
    assert out["agents"]["badge"] == "agents_neutral"


def test_veto_buy_boost():
    out = apply_agent_veto(gate_passed=True, gate_reasons=[], rating="Overweight")
    assert out["passed"] is True
    assert out["status"] == "boost"


def test_veto_review_fail_open():
    out = apply_agent_veto(gate_passed=True, gate_reasons=[], rating="REVIEW")
    assert out["passed"] is True
    assert out["agents"]["unavailable"] is True


def test_to_yahoo_ticker():
    from research.adapter import to_yahoo_ticker

    assert to_yahoo_ticker("RELIANCE") == "RELIANCE.NS"
    assert to_yahoo_ticker("RELIANCE.NS") == "RELIANCE.NS"
    assert to_yahoo_ticker("NIFTY") == "^NSEI"
    assert to_yahoo_ticker("BANKNIFTY", fno_underlying=True) == "^NSEBANK"


@pytest.mark.django_db
def test_build_portfolio_flat_is_empty_list():
    from portfolio.models import PortfolioSettings
    from research.adapter import build_portfolio_context

    PortfolioSettings.get_solo()
    book = build_portfolio_context(known=True)
    assert book is not None
    assert book["currency"] == "INR"
    assert book["positions"] == []
    assert build_portfolio_context(known=False) is None


@pytest.mark.django_db
def test_run_analysis_mocked_ok(settings):
    from research.models import ResearchRun
    from research.services import run_analysis

    fake = {
        "ok": True,
        "rating": "Buy",
        "decision": "Buy",
        "summary": {
            "final_trade_decision": "Buy on strength",
            "investment_debate_state": {
                "bull_history": "bull case",
                "bear_history": "bear case",
            },
        },
        "report_html_path": "artifacts/research/x/complete_report.html",
        "report_dir": "artifacts/research/x",
        "config_snapshot": {"llm_provider": "deepseek"},
        "analysts": ["market", "news"],
    }
    with (
        patch("research.services.agents_enabled", return_value=True),
        patch("research.services.provider_keys_present", return_value=True),
        patch("research.services.invoke_runner", return_value=fake),
        patch("research.services.under_daily_caps", return_value=(True, "")),
    ):
        run = run_analysis("RELIANCE", as_of=date(2026, 11, 20), sync=True)
    assert run.status == ResearchRun.Status.OK
    assert run.rating == "Buy"
    assert run.yahoo_ticker == "RELIANCE.NS"
    assert "ta_config" in (run.config_snapshot or {})


@pytest.mark.django_db
def test_run_analysis_fail_open_on_error():
    from marketdata.models import Instrument
    from research.models import ResearchRun
    from research.services import run_analysis
    from signals.models import Signal

    inst = Instrument.objects.create(symbol="TCS.NS")
    sig = Signal.objects.create(
        instrument=inst,
        side="BUY",
        strategy="swing",
        product="CNC",
        data_as_of="2026-11-20T10:00:00+05:30",
        data_source="yfinance",
        valid_until="2026-11-20T15:30:00+05:30",
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
        status=Signal.Status.NEW,
        chase_limit=101,
        gate={"result": "PASS", "ok": True, "blocked_by": []},
        pipeline={},
    )
    with (
        patch("research.services.agents_enabled", return_value=True),
        patch("research.services.provider_keys_present", return_value=True),
        patch(
            "research.services.invoke_runner",
            return_value={"ok": False, "error": "boom"},
        ),
        patch("research.services.under_daily_caps", return_value=(True, "")),
    ):
        run = run_analysis("TCS.NS", as_of=date(2026, 11, 20), signal=sig, sync=True)
    assert run.status == ResearchRun.Status.ERROR
    sig.refresh_from_db()
    assert sig.gate.get("result") == "PASS"  # fail-open: still PASS
    assert (sig.pipeline or {}).get("agents", {}).get("badge") == "agents_unavailable"


@pytest.mark.django_db
def test_sell_veto_downgrades_signal():
    from marketdata.models import Instrument
    from research.services import run_analysis
    from signals.models import Signal

    inst = Instrument.objects.create(symbol="INFY.NS")
    sig = Signal.objects.create(
        instrument=inst,
        side="BUY",
        strategy="swing",
        product="CNC",
        data_as_of="2026-11-20T10:00:00+05:30",
        data_source="yfinance",
        valid_until="2026-11-20T15:30:00+05:30",
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
        probability=0.62,
        status=Signal.Status.NEW,
        chase_limit=101,
        gate={"result": "PASS", "ok": True, "blocked_by": []},
        pipeline={},
    )
    fake = {
        "ok": True,
        "rating": "Sell",
        "decision": "Sell",
        "summary": {"final_trade_decision": "Avoid"},
        "report_html_path": "",
        "report_dir": "",
        "analysts": ["market"],
    }
    with (
        patch("research.services.agents_enabled", return_value=True),
        patch("research.services.provider_keys_present", return_value=True),
        patch("research.services.invoke_runner", return_value=fake),
        patch("research.services.under_daily_caps", return_value=(True, "")),
    ):
        run_analysis("INFY.NS", as_of=date(2026, 11, 20), signal=sig, sync=True)
    sig.refresh_from_db()
    assert sig.gate.get("result") == "BLOCK"
    assert "agents_veto" in (sig.gate.get("blocked_by") or [])
    assert sig.status == Signal.Status.SKIPPED
    assert sig.skip_reason == "agents_veto"


def test_allow_broker_orders_still_false():
    from config import trading_config as cfg

    assert cfg.ALLOW_BROKER_ORDERS is False
