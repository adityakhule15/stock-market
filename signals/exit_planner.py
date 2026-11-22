"""Exit planner — stop/target BEFORE entry + forced square-off for intraday."""

from __future__ import annotations

from typing import Any

from config import trading_config as cfg
from signals.risk import round_tick


def plan_exits(
    *,
    entry: float,
    side: str = "BUY",
    or_high: float | None = None,
    or_low: float | None = None,
    atr: float | None = None,
    stop_mode: str = "auto",  # auto | range_mid | atr | or_low
    t1_r: float | None = None,
    t2_r: float | None = None,
    intraday: bool = False,
    cfg_mod=cfg,
) -> dict[str, Any]:
    t1_r = cfg_mod.T1_R if t1_r is None else t1_r
    t2_r = cfg_mod.T2_R if t2_r is None else t2_r
    s = 1 if side == "BUY" else -1

    stop = None
    stop_method = stop_mode
    if stop_mode == "auto":
        if or_high is not None and or_low is not None:
            stop = (or_high + or_low) / 2.0
            stop_method = "range_mid"
        elif atr is not None and atr > 0:
            stop = entry - s * cfg_mod.ATR_SL_MULT * atr
            stop_method = "atr"
        else:
            stop = entry * (1 - s * 0.01)
            stop_method = "pct_1"
    elif stop_mode == "range_mid" and or_high is not None and or_low is not None:
        stop = (or_high + or_low) / 2.0
    elif stop_mode == "or_low" and or_low is not None:
        stop = or_low
    elif atr is not None:
        stop = entry - s * cfg_mod.ATR_SL_MULT * atr
        stop_method = "atr"
    else:
        stop = entry * (1 - s * 0.01)
        stop_method = "pct_1"

    stop = round_tick(float(stop), "down" if side == "BUY" else "up")
    R = abs(entry - stop)
    if R <= 0:
        return {"ok": False, "reason": "invalid stop geometry (R<=0)"}

    target1 = round_tick(entry + s * t1_r * R, "up" if side == "BUY" else "down")
    target2 = round_tick(entry + s * t2_r * R, "up" if side == "BUY" else "down")

    out: dict[str, Any] = {
        "ok": True,
        "sl_trigger": stop,
        "sl_limit": round_tick(stop * (1 - s * cfg_mod.SL_BUFFER), "down" if side == "BUY" else "up"),
        "target1": target1,
        "target2": target2,
        "R": R,
        "stop_method": stop_method,
        "t1_r": t1_r,
        "t2_r": t2_r,
        "reason": f"stop via {stop_method}; targets {t1_r}R / {t2_r}R",
    }
    if intraday:
        out["square_off_by"] = cfg_mod.SQUARE_OFF_BY
        out["reason"] += f"; square off by {cfg_mod.SQUARE_OFF_BY} IST"
    return out
