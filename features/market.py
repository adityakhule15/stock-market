"""NIFTY / VIX / breadth market context features for ML + regime gate."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from config import trading_config as cfg

logger = logging.getLogger(__name__)


def load_market_frames() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Return (nifty daily, vix daily) or (None, None)."""
    try:
        from marketdata.service import MarketDataService

        svc = MarketDataService()
        nifty, _ = svc.history("^NSEI", interval="1d", period="5y")
        try:
            vix, _ = svc.history("^INDIAVIX", interval="1d", period="5y")
        except Exception:  # noqa: BLE001
            vix = None
        return nifty, vix
    except Exception as exc:  # noqa: BLE001
        logger.debug("load_market_frames: %s", exc)
        return None, None


def build_market_ctx(
    index: pd.DatetimeIndex,
    nifty: pd.DataFrame | None = None,
    vix: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Columns aligned to `index`: nifty_ret_*, nifty_above_ema50, vix_*, breadth stub."""
    if nifty is None and vix is None:
        nifty, vix = load_market_frames()
    out = pd.DataFrame(index=index)
    if nifty is not None and not nifty.empty:
        n = nifty.copy()
        n.index = pd.to_datetime(n.index).tz_localize(None) if getattr(n.index, "tz", None) else pd.to_datetime(n.index)
        c = n["close"].astype(float)
        ema50 = c.ewm(span=50, adjust=False).mean()
        ema50_slope = ema50.pct_change(5)
        out["nifty_ret_1"] = c.pct_change(1).reindex(index).ffill()
        out["nifty_ret_5"] = c.pct_change(5).reindex(index).ffill()
        out["nifty_ret_20"] = c.pct_change(20).reindex(index).ffill()
        out["nifty_above_ema50"] = (c > ema50).astype(float).reindex(index).ffill()
        out["nifty_ema50_slope"] = ema50_slope.reindex(index).ffill()
    else:
        for col in ("nifty_ret_1", "nifty_ret_5", "nifty_ret_20", "nifty_above_ema50", "nifty_ema50_slope"):
            out[col] = 0.0

    if vix is not None and not vix.empty:
        v = vix.copy()
        v.index = pd.to_datetime(v.index).tz_localize(None) if getattr(v.index, "tz", None) else pd.to_datetime(v.index)
        vc = v["close"].astype(float)
        out["vix_level"] = vc.reindex(index).ffill()
        out["vix_chg_5"] = vc.pct_change(5).reindex(index).ffill()
    else:
        out["vix_level"] = float(getattr(cfg, "VIX_LOW", 12.0))
        out["vix_chg_5"] = 0.0

    # Breadth stub (fraction of universe up) — 0.5 when unknown
    out["breadth_up"] = 0.5
    return out.fillna(0.0)


def relative_strength(stock_close: pd.Series, nifty: pd.DataFrame | None) -> pd.Series:
    if nifty is None or nifty.empty:
        return pd.Series(0.0, index=stock_close.index)
    nc = nifty["close"].astype(float)
    nc = nc.reindex(stock_close.index).ffill()
    stock_ret = stock_close.pct_change(20)
    nifty_ret = nc.pct_change(20)
    return (stock_ret - nifty_ret).fillna(0.0)


def nifty_long_ok(cfg_mod=cfg) -> dict[str, Any]:
    """Regime gate: skip new stock longs when NIFTY below falling EMA50."""
    nifty, _ = load_market_frames()
    if nifty is None or len(nifty) < 60:
        return {"ok": True, "reason": "nifty data unavailable — allow", "tag": "unknown"}
    c = nifty["close"].astype(float)
    ema50 = c.ewm(span=50, adjust=False).mean()
    slope = float(ema50.iloc[-1] / ema50.iloc[-6] - 1) if len(ema50) > 6 else 0.0
    above = float(c.iloc[-1]) > float(ema50.iloc[-1])
    if not above and slope < 0:
        return {
            "ok": False,
            "reason": "NIFTY below falling 50-day average — skip new stock longs",
            "tag": "bear_nifty",
            "nifty_above_ema50": False,
            "nifty_ema50_slope": slope,
        }
    return {
        "ok": True,
        "reason": "NIFTY trend OK for longs" if above else "NIFTY below EMA50 but slope not falling",
        "tag": "ok",
        "nifty_above_ema50": above,
        "nifty_ema50_slope": slope,
    }
