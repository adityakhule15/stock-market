"""Multi-pattern day scanner: ORB, bull flag, oversold bounce (NSE/IST)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from config import trading_config as cfg
from scanner.orb import opening_range, orb_plan
from signals.risk import TICK, round_tick


def _rsi14(closes: pd.Series) -> float:
    if len(closes) < 15:
        return float("nan")
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def detect_bull_flag(daily: pd.DataFrame) -> dict[str, Any] | None:
    """Close-only: >10% run then 5d consolidation within ~5% range."""
    if daily is None or len(daily) < 25:
        return None
    c = daily["close"].astype(float)
    run = c.iloc[-15:-5]
    if float(run.iloc[-1] / run.iloc[0] - 1) < 0.10:
        return None
    cons = c.iloc[-5:]
    hi, lo = float(cons.max()), float(cons.min())
    if lo <= 0 or (hi - lo) / lo > 0.05:
        return None
    trig = round_tick(hi + TICK, "up")
    stop = round_tick(lo, "down")
    return {
        "setup": "bull_flag",
        "side": "BUY",
        "entry_trigger": trig,
        "sl_trigger": stop,
        "or_high": hi,
        "or_low": lo,
    }


def detect_oversold_bounce(daily: pd.DataFrame) -> dict[str, Any] | None:
    """RSI14 was <30 yesterday; closed up today."""
    if daily is None or len(daily) < 20:
        return None
    c = daily["close"].astype(float)
    rsi_y = _rsi14(c.iloc[:-1])
    if not (rsi_y < 30):
        return None
    if float(c.iloc[-1]) <= float(c.iloc[-2]):
        return None
    atr = float((daily["high"] - daily["low"]).astype(float).tail(14).mean())
    entry = float(c.iloc[-1])
    stop = round_tick(entry - cfg.ATR_SL_MULT * atr, "down")
    return {
        "setup": "oversold_bounce",
        "side": "BUY",
        "entry_trigger": round_tick(entry * (1 + cfg.ENTRY_BUFFER), "up"),
        "sl_trigger": stop,
        "rsi_yesterday": rsi_y,
    }


def scan_symbol(
    symbol: str,
    *,
    bars_5m_today: pd.DataFrame | None = None,
    daily: pd.DataFrame | None = None,
    avg_or_volume: float = 0.0,
    prev_close: float = 0.0,
    nifty_ok: bool = True,
    cfg_mod=cfg,
) -> list[dict[str, Any]]:
    """Return ranked pattern hits for one symbol (best first)."""
    hits: list[dict[str, Any]] = []

    if bars_5m_today is not None and not bars_5m_today.empty and cfg_mod.INTRADAY_ENABLED:
        plan = orb_plan(symbol, bars_5m_today, avg_or_volume, prev_close, nifty_ok, cfg_mod)
        if plan:
            rng = opening_range(bars_5m_today)
            hits.append(
                {
                    "setup": "orb",
                    "side": plan["side"],
                    "entry_trigger": plan["entry_trigger"],
                    "sl_trigger": plan["sl_trigger"],
                    "target1": plan["target1"],
                    "target2": plan["target2"],
                    "product": plan["product"],
                    "strategy": "intraday",
                    "or_high": rng[0] if rng else None,
                    "or_low": rng[1] if rng else None,
                    "square_off_by": plan.get("square_off_by", cfg_mod.SQUARE_OFF_BY),
                    "liquidity": float(bars_5m_today["volume"].sum()) if "volume" in bars_5m_today else 0.0,
                }
            )

    flag = detect_bull_flag(daily) if daily is not None else None
    if flag:
        flag["symbol"] = symbol
        flag["product"] = "CNC"
        flag["strategy"] = "swing"
        flag["liquidity"] = float(daily["volume"].tail(20).mean()) if "volume" in daily else 0.0
        hits.append(flag)

    bounce = detect_oversold_bounce(daily) if daily is not None else None
    if bounce:
        bounce["symbol"] = symbol
        bounce["product"] = "CNC"
        bounce["strategy"] = "swing"
        bounce["liquidity"] = float(daily["volume"].tail(20).mean()) if "volume" in daily else 0.0
        hits.append(bounce)

    hits.sort(key=lambda h: h.get("liquidity", 0.0), reverse=True)
    for h in hits:
        h.setdefault("symbol", symbol)
    return hits


def scanner_stage(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Pipeline stage payload."""
    if not hits:
        return {"ok": False, "setup": None, "hits": [], "reason": "no pattern"}
    best = hits[0]
    return {
        "ok": True,
        "setup": best.get("setup"),
        "side": best.get("side"),
        "entry_trigger": best.get("entry_trigger"),
        "sl_trigger": best.get("sl_trigger"),
        "hits": [{"setup": h.get("setup"), "liquidity": h.get("liquidity")} for h in hits[:5]],
        "reason": f"{best.get('setup')} ranked #1",
    }
