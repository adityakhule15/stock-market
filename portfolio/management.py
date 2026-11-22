"""Live position management suggestions — SL tighten-only, never widen."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from config import trading_config as cfg
from signals.risk import round_tick

IST = ZoneInfo("Asia/Kolkata")

ACTIONS = (
    "HOLD",
    "MOVE_SL_BREAKEVEN",
    "TRAIL_SL",
    "BOOK_PARTIAL",
    "RAISE_TARGET",
    "EXIT_NOW",
)


def _r_multiple(entry: float, stop: float, mark: float, side: str = "BUY") -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    if side == "BUY":
        return (mark - entry) / risk
    return (entry - mark) / risk


def tighten_stop(current_sl: float, proposed_sl: float, side: str = "BUY") -> float:
    """Never widen SL — only tighten (BUY: higher SL; SELL: lower SL)."""
    if side == "BUY":
        return max(float(current_sl), float(proposed_sl))
    return min(float(current_sl), float(proposed_sl))


def suggest_management(
    *,
    entry: float,
    stop: float,
    target: float,
    mark: float,
    side: str = "BUY",
    atr: float | None = None,
    is_option: bool = False,
    now: datetime | None = None,
    cfg_mod=cfg,
) -> dict[str, Any]:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    r = _r_multiple(entry, stop, mark, side)
    action = "HOLD"
    reason = "price between stop and target"
    new_sl = float(stop)
    new_target = float(target)

    adverse_r = float(getattr(cfg_mod, "ADVERSE_R_EXIT", -0.50))
    prem_drop = float(getattr(cfg_mod, "FNO_PREMIUM_DROP_EXIT", 0.20))
    premium_pct = 0.0
    if entry > 0:
        if side == "BUY":
            premium_pct = (float(mark) - float(entry)) / float(entry)
        else:
            premium_pct = (float(entry) - float(mark)) / float(entry)

    # Forced exit near square-off — F&O / options only (CNC may hold multi-day)
    if is_option and now.time() >= time(15, 10):
        action = "EXIT_NOW"
        reason = "forced F&O square-off window (15:10 IST)"
    elif is_option and now.time() >= time(15, 0):
        action = "EXIT_NOW"
        reason = "approaching F&O square-off — exit now"
    elif side == "BUY" and mark <= stop:
        action = "EXIT_NOW"
        reason = "mark at/through stop"
    elif side == "BUY" and mark >= target:
        action = "EXIT_NOW"
        reason = "mark at/through target"
    elif side == "SELL" and mark >= stop:
        action = "EXIT_NOW"
        reason = "mark at/through stop"
    elif side == "SELL" and mark <= target:
        action = "EXIT_NOW"
        reason = "mark at/through target"
    # Deep adverse move — do not HOLD waiting for a far SL on bleeding options
    elif r <= adverse_r:
        action = "EXIT_NOW"
        reason = (
            f"adverse {r:.2f}R (≤ {adverse_r:.2f}R) — cut loss; SL still far at {stop:.2f}"
        )
    elif is_option and premium_pct <= -prem_drop:
        action = "EXIT_NOW"
        reason = (
            f"option premium down {abs(premium_pct)*100:.0f}% from entry "
            f"(threshold {prem_drop*100:.0f}%) — exit, do not average"
        )
    elif r >= 1.5:
        # Trail by ATR after +1.5R
        atr_v = float(atr or abs(entry - stop) * 0.5)
        if side == "BUY":
            proposed = round_tick(mark - atr_v, "down")
        else:
            proposed = round_tick(mark + atr_v, "up")
        new_sl = tighten_stop(stop, proposed, side)
        action = "TRAIL_SL"
        reason = f"+{r:.2f}R — trail SL by ATR to {new_sl:.2f}"
        # Optionally raise target with momentum
        if atr_v > 0:
            bump = round_tick(target + (atr_v if side == "BUY" else -atr_v), "up" if side == "BUY" else "down")
            if (side == "BUY" and bump > target) or (side == "SELL" and bump < target):
                new_target = bump
                action = "RAISE_TARGET"
                reason = f"+{r:.2f}R — trail SL {new_sl:.2f} and raise target to {new_target:.2f}"
    elif r >= 1.0:
        new_sl = tighten_stop(stop, entry, side)
        action = "MOVE_SL_BREAKEVEN"
        reason = f"+{r:.2f}R — move SL to breakeven {new_sl:.2f}"
    elif r >= 0.75:
        action = "BOOK_PARTIAL"
        reason = f"+{r:.2f}R — consider booking partial profits"
    elif is_option and now.time() >= time(14, 30):
        action = "HOLD"
        reason = "option buy — theta/time-decay rising into close; watch closely"

    # Hard rule: proposed SL must not widen
    new_sl = tighten_stop(stop, new_sl, side)
    if side == "BUY" and new_sl < float(stop) - 1e-9:
        new_sl = float(stop)
    if side == "SELL" and new_sl > float(stop) + 1e-9:
        new_sl = float(stop)

    return {
        "action": action,
        "reason": reason,
        "suggested_sl": round(float(new_sl), 2),
        "suggested_target": round(float(new_target), 2),
        "r_multiple": round(r, 3),
        "sl_widened": False,
    }


def apply_user_levels(
    *,
    old_sl: float,
    old_tgt: float,
    new_sl: float,
    new_tgt: float,
    side: str = "BUY",
) -> dict[str, Any]:
    """Record user-confirmed Groww update; detect if they widened SL."""
    widened = (side == "BUY" and new_sl < old_sl - 1e-9) or (side == "SELL" and new_sl > old_sl + 1e-9)
    return {
        "old_sl": old_sl,
        "new_sl": new_sl,
        "old_tgt": old_tgt,
        "new_tgt": new_tgt,
        "widened": widened,
        "source": "user_groww",
    }
