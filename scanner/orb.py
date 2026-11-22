from __future__ import annotations

import pandas as pd

from config import trading_config as cfg
from signals.risk import TICK, position_size, round_tick


def opening_range(bars_5m_today: pd.DataFrame):
    """Opening range from 09:15, 09:20, 09:25 bars (inclusive through 09:29)."""
    if bars_5m_today.empty:
        return None
    idx = bars_5m_today.index
    if idx.tz is None:
        bars = bars_5m_today.copy()
        bars.index = bars.index.tz_localize("Asia/Kolkata")
    else:
        bars = bars_5m_today.tz_convert("Asia/Kolkata")
    r = bars.between_time("09:15", "09:29")
    if r.empty:
        return None
    return float(r["high"].max()), float(r["low"].min()), float(r["volume"].sum())


def orb_plan(symbol, bars_today, avg_or_volume, prev_close, nifty_ok, cfg_mod=cfg):
    if not cfg_mod.INTRADAY_ENABLED:
        return None
    rng = opening_range(bars_today)
    if rng is None:
        return None
    hi, lo, vol = rng
    width = (hi - lo) / hi if hi else 0
    if not (cfg_mod.ORB_MIN_WIDTH <= width <= cfg_mod.ORB_MAX_WIDTH):
        return None
    if vol < cfg_mod.ORB_VOL_MULT * avg_or_volume:
        return None
    if not (hi > prev_close and nifty_ok):
        return None
    trig = round_tick(hi + TICK, "up")
    lim = round_tick(trig * (1 + cfg_mod.ENTRY_BUFFER), "up")
    stop = round_tick((hi + lo) / 2, "down")
    R = trig - stop
    if R <= 0:
        return None
    qty = position_size(trig, stop, cfg_mod.CAPITAL, cfg_mod.RISK_PER_TRADE, cfg_mod.MAX_POSITION_PCT)
    if qty < 1:
        return None
    return dict(
        symbol=symbol,
        side="BUY",
        product="MIS",
        strategy="intraday",
        entry_trigger=trig,
        entry_limit=lim,
        sl_trigger=stop,
        sl_limit=round_tick(stop * (1 - cfg_mod.SL_BUFFER), "down"),
        target1=round_tick(trig + cfg_mod.T1_R * R, "down"),
        target2=round_tick(trig + cfg_mod.T2_R * R, "down"),
        qty=qty,
        cancel_after="14:00",
        square_off_by="15:10",
    )
