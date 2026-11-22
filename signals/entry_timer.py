"""Entry timer — WAITING / TRIGGERED / EXPIRED on closed bars only."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from config import trading_config as cfg

IST = ZoneInfo("Asia/Kolkata")


def evaluate_entry(
    *,
    entry_trigger: float,
    side: str = "BUY",
    bars: pd.DataFrame | None = None,
    now: datetime | None = None,
    expire_after: time | None = None,
    require_closed_bar: bool = True,
) -> dict[str, Any]:
    """
    Trigger only when a *completed* bar closes beyond the trigger (wick alone ≠ entry).
    """
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    expire_after = expire_after or time(14, 0)
    if now.time() > expire_after and now.weekday() < 5:
        return {
            "status": "EXPIRED",
            "ok": False,
            "reason": f"past cancel window {expire_after.strftime('%H:%M')} IST",
            "message": "enter only when trigger hits — window expired",
        }

    if bars is None or bars.empty:
        return {
            "status": "WAITING",
            "ok": True,
            "reason": "no bars yet — waiting for trigger",
            "message": "enter only when trigger hits",
        }

    df = bars.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)
    else:
        df.index = df.index.tz_convert(IST)

    # Drop forming bar if require_closed_bar (last bar still open while market open)
    closes = df["close"].astype(float)
    if require_closed_bar and len(closes) >= 2 and now.time() < time(15, 30):
        # Assume last row may be forming during session; use prior closed bar for confirm
        check = closes.iloc[:-1]
    else:
        check = closes

    if check.empty:
        return {
            "status": "WAITING",
            "ok": True,
            "reason": "waiting for first closed bar",
            "message": "enter only when trigger hits",
        }

    last = float(check.iloc[-1])
    hit = last >= float(entry_trigger) if side == "BUY" else last <= float(entry_trigger)
    if hit:
        return {
            "status": "TRIGGERED",
            "ok": True,
            "reason": f"closed bar {last:.2f} crossed trigger {entry_trigger:.2f}",
            "message": "trigger hit on closed bar — you may enter in Groww",
            "close": last,
        }
    return {
        "status": "WAITING",
        "ok": True,
        "reason": f"last close {last:.2f} has not crossed {entry_trigger:.2f}",
        "message": "enter only when trigger hits",
        "close": last,
    }
