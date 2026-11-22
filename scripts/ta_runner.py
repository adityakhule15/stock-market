#!/usr/bin/env python3
"""TradingAgents sidecar runner — JSON stdin → JSON stdout.

Designed to run under `.venv-ta` (or Docker research image) so pandas 3 /
langchain stay out of the Django process.

Request (stdin JSON):
  {
    "action": "analyze" | "settle" | "backtest" | "ping",
    "ticker": "RELIANCE.NS",
    "date": "2026-11-20",
    "analysts": ["market","social","news","fundamentals"],
    "portfolio": {"cash": 2000, "currency": "INR", "positions": []},
    "config": {...},          # optional overlays on DEFAULT_CONFIG
    "selected_analysts": ...,
    "checkpoint_enabled": true,
    "save_html": true,
    "tickers": ["RELIANCE.NS"],  # backtest
    "start": "2024-01-01",
    "end": "2024-06-01",
    "every": 7,
    "run_id": null
  }

Response (stdout JSON): status, rating, report paths, errors, etc.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any


def _ok(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps({"ok": True, **payload}, default=str))
    sys.stdout.write("\n")
    return 0


def _fail(message: str, **extra: Any) -> int:
    sys.stdout.write(json.dumps({"ok": False, "error": message, **extra}, default=str))
    sys.stdout.write("\n")
    return 1


def _load_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("empty stdin — expected JSON request")
    return json.loads(raw)


def _build_config(overlays: dict[str, Any] | None) -> dict[str, Any]:
    from tradingagents.default_config import DEFAULT_CONFIG, build_default_config

    # Re-read env so TRADINGAGENTS_* set by the parent apply now
    cfg = build_default_config() if callable(build_default_config) else DEFAULT_CONFIG.copy()
    if overlays:
        for k, v in overlays.items():
            if v is not None:
                cfg[k] = v
    # Ensure project paths win if parent passed them
    return cfg


def _portfolio_from(req: dict[str, Any]):
    portfolio = req.get("portfolio")
    if portfolio is None:
        return None
    from tradingagents.portfolio import PortfolioContext

    return PortfolioContext.model_validate(portfolio)


def action_ping(req: dict[str, Any]) -> int:
    try:
        import tradingagents

        ver = getattr(tradingagents, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"import failed: {exc}")
    return _ok({"action": "ping", "version": ver, "python": sys.executable})


def action_analyze(req: dict[str, Any]) -> int:
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    ticker = (req.get("ticker") or "").strip()
    trade_date = (req.get("date") or "").strip() or date.today().isoformat()
    if not ticker:
        return _fail("ticker required")

    analysts = req.get("selected_analysts") or req.get("analysts") or (
        "market",
        "social",
        "news",
        "fundamentals",
    )
    if isinstance(analysts, str):
        analysts = tuple(a.strip() for a in analysts.split(",") if a.strip())
    else:
        analysts = tuple(analysts)

    cfg = _build_config(req.get("config") if isinstance(req.get("config"), dict) else None)
    if req.get("checkpoint_enabled") is not None:
        cfg["checkpoint_enabled"] = bool(req["checkpoint_enabled"])
    if req.get("benchmark_ticker"):
        cfg["benchmark_ticker"] = req["benchmark_ticker"]

    portfolio = _portfolio_from(req)
    save_html = bool(req.get("save_html", True))
    asset_type = (req.get("asset_type") or "stock").strip() or "stock"

    ta = TradingAgentsGraph(
        selected_analysts=analysts,
        debug=bool(req.get("debug", False)),
        config=cfg,
    )
    state, decision = ta.propagate(
        ticker, trade_date, asset_type=asset_type, portfolio=portfolio
    )

    report_dir = None
    html_path = None
    try:
        path = ta.save_reports(state, ticker, html=save_html)
        report_dir = str(path)
        candidate = Path(path) / "complete_report.html"
        if candidate.exists():
            html_path = str(candidate)
    except Exception as exc:  # noqa: BLE001
        report_err = str(exc)
    else:
        report_err = None

    # Extract useful snippets for UI without dumping huge state
    summary = {
        "market_report": _clip(state.get("market_report")),
        "sentiment_report": _clip(state.get("sentiment_report")),
        "news_report": _clip(state.get("news_report")),
        "fundamentals_report": _clip(state.get("fundamentals_report")),
        "investment_debate_state": _debate_clip(state.get("investment_debate_state")),
        "risk_debate_state": _debate_clip(state.get("risk_debate_state")),
        "trader_investment_plan": _clip(state.get("trader_investment_plan")),
        "final_trade_decision": _clip(state.get("final_trade_decision")),
        "investment_plan": _clip(state.get("investment_plan")),
    }

    return _ok(
        {
            "action": "analyze",
            "ticker": ticker,
            "date": trade_date,
            "rating": decision,
            "decision": decision,
            "report_dir": report_dir,
            "report_html_path": html_path,
            "report_error": report_err,
            "summary": summary,
            "config_snapshot": {
                k: v
                for k, v in cfg.items()
                if k
                not in {
                    # keep snapshot useful but avoid huge lists if any
                }
            },
            "analysts": list(analysts),
        }
    )


def _clip(value: Any, n: int = 4000) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value if len(value) <= n else value[: n - 20] + "\n…[truncated]"
    if isinstance(value, dict):
        return {k: _clip(v, n) for k, v in value.items()}
    return value


def _debate_clip(debate: Any) -> Any:
    if not isinstance(debate, dict):
        return _clip(debate)
    keys = (
        "bull_history",
        "bear_history",
        "history",
        "current_response",
        "judge_decision",
        "bull_advantage",
        "bear_advantage",
        "risky_history",
        "safe_history",
        "neutral_history",
        "latest_speaker",
    )
    out = {}
    for k in keys:
        if k in debate:
            out[k] = _clip(debate.get(k), 2500)
    # keep count fields
    for k, v in debate.items():
        if isinstance(v, (int, float, bool)) or k.endswith("_count"):
            out[k] = v
    return out


def action_settle(req: dict[str, Any]) -> int:
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    cfg = _build_config(req.get("config") if isinstance(req.get("config"), dict) else None)
    ta = TradingAgentsGraph(config=cfg)
    wait = bool(req.get("wait", True))
    ticker = (req.get("ticker") or "").strip()
    if ticker:
        result = ta.settle_pending(ticker)
    else:
        result = ta.settle_all_pending(wait=wait)

    settled = getattr(result, "settled", None) or []
    failed = getattr(result, "failed", None) or []
    return _ok(
        {
            "action": "settle",
            "settled": [{"ticker": t, "date": d} for t, d in settled],
            "failed": [
                {"ticker": t, "date": d, "reason": r} for t, d, r in failed
            ],
        }
    )


def action_backtest(req: dict[str, Any]) -> int:
    from tradingagents.backtest import iter_grid, run_backtest, summarize

    tickers = req.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [t.strip() for t in tickers.split(",") if t.strip()]
    if not tickers:
        return _fail("tickers required")

    start = req.get("start")
    end = req.get("end")
    if not start or not end:
        return _fail("start and end required (YYYY-MM-DD)")
    every = int(req.get("every") or 7)
    dates = iter_grid(start, end, every)

    analysts = req.get("selected_analysts") or req.get("analysts")
    if isinstance(analysts, str):
        analysts = tuple(a.strip() for a in analysts.split(",") if a.strip())
    elif analysts:
        analysts = tuple(analysts)
    else:
        analysts = ("market", "social", "news", "fundamentals")

    cfg = _build_config(req.get("config") if isinstance(req.get("config"), dict) else None)
    portfolio = _portfolio_from(req)
    run_id = req.get("run_id")
    asset_type = (req.get("asset_type") or "stock").strip() or "stock"

    result = run_backtest(
        list(tickers),
        dates,
        cfg,
        asset_type=asset_type,
        portfolio=portfolio,
        selected_analysts=analysts,
        run_id=run_id,
    )
    summary = summarize(result)
    rendered = summary.render() if hasattr(summary, "render") else str(summary)

    return _ok(
        {
            "action": "backtest",
            "run_id": getattr(result, "run_id", run_id),
            "log_path": str(getattr(result, "log_path", "") or ""),
            "cells_run": getattr(result, "cells_run", None),
            "skipped": getattr(result, "skipped", None),
            "summary_text": rendered,
            "failures": [
                {"ticker": t, "date": d, "reason": r}
                for t, d, r in (getattr(result, "failures", None) or [])
            ],
            "settlement_failures": [
                {"ticker": t, "reason": r}
                for t, r in (getattr(result, "settlement_failures", None) or [])
            ],
        }
    )


def main() -> int:
    os.environ.setdefault("TRADINGAGENTS_NO_UPDATE_CHECK", "1")
    try:
        req = _load_request()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"invalid request JSON: {exc}")

    action = (req.get("action") or "analyze").strip().lower()
    try:
        if action == "ping":
            return action_ping(req)
        if action == "analyze":
            return action_analyze(req)
        if action == "settle":
            return action_settle(req)
        if action == "backtest":
            return action_backtest(req)
        return _fail(f"unknown action: {action}")
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc), traceback=traceback.format_exc()[-4000:])


if __name__ == "__main__":
    raise SystemExit(main())
