"""Market regime from NIFTY + India VIX: trend / range / high_vol."""

from __future__ import annotations

import logging
from typing import Any

from config import trading_config as cfg

logger = logging.getLogger(__name__)


def detect_regime(cfg_mod=cfg) -> dict[str, Any]:
    """Best-effort regime tag. Defaults to 'range' if data missing."""
    vix = None
    nifty_ret = None
    nifty_atr_ratio = None
    try:
        from marketdata.service import MarketDataService

        mds = MarketDataService()
        # Prefer yfinance tickers
        for sym, key in (("^INDIAVIX", "vix"), ("^NSEI", "nifty")):
            try:
                df, _src = mds.history(sym, period="3mo", interval="1d")
            except Exception:  # noqa: BLE001
                df = None
            if df is None or getattr(df, "empty", True) or len(df) < 20:
                continue
            closes = df["close"].astype(float)
            if key == "vix":
                vix = float(closes.iloc[-1])
            else:
                atr = float((df["high"] - df["low"]).astype(float).tail(14).mean())
                prev = float(closes.iloc[-2])
                last = float(closes.iloc[-1])
                nifty_ret = (last / prev - 1.0) if prev else 0.0
                nifty_atr_ratio = abs(last - prev) / atr if atr > 0 else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("detect_regime: %s", exc)

    tag = "range"
    reason = "default range (insufficient data)"
    if vix is not None and vix >= cfg_mod.VIX_HIGH:
        tag = "high_vol"
        reason = f"India VIX {vix:.1f} ≥ {cfg_mod.VIX_HIGH}"
    elif nifty_atr_ratio is not None and nifty_atr_ratio >= 1.0 and (vix is None or vix < cfg_mod.VIX_HIGH):
        tag = "trend"
        direction = "up" if (nifty_ret or 0) > 0 else "down"
        reason = f"NIFTY day move {nifty_atr_ratio:.2f}×ATR ({direction})"
    elif nifty_atr_ratio is not None and nifty_atr_ratio < cfg_mod.NIFTY_RANGE_ATR_MULT:
        tag = "range"
        reason = f"NIFTY quiet ({nifty_atr_ratio:.2f}×ATR)"
    elif vix is not None and vix <= cfg_mod.VIX_LOW:
        tag = "trend"
        reason = f"India VIX {vix:.1f} low — trend-friendly"

    return {
        "tag": tag,
        "vix": vix,
        "nifty_ret": nifty_ret,
        "nifty_atr_ratio": nifty_atr_ratio,
        "reason": reason,
        "no_trade_bias": tag == "high_vol",
    }
