"""Turn gate reason codes into plain sentences for the UI."""

from __future__ import annotations

from typing import Any


_MAP = {
    "coin_flip": "Odds look like a coin flip — skip",
    "fno_score_weak": "Options score is weak — skip",
    "rr_below_min": "Reward after charges is too small versus the risk",
    "regime_high_vol": "Market is high-vol — skip same-day trades",
    "nifty_bear_skip_longs": "NIFTY is below a falling 50-day average — no new stock buys",
    "budget_failed": "Does not fit the wallet after charges",
    "kill switch is ON": "Kill switch is on — turn it off in Settings",
    "portfolio DEAD — equity at/below floor; staff reset required": "Wallet hit the stop-trading floor",
    "COIN FLIP — skip": "Odds look like a coin flip — skip",
}


def plain_reason(code: str) -> str:
    c = str(code or "").strip()
    if not c:
        return ""
    if c in _MAP:
        return _MAP[c]
    if c.startswith("ev_below_min:"):
        return f"Expected profit after charges is only ₹{c.split(':', 1)[1]} — below the minimum"
    if c.startswith("fno_loss_cap:"):
        return f"Loss at stop would exceed the options cap (₹{c.split(':', 1)[1]})"
    if c.startswith("time_machine:"):
        return f"Time Machine says {c.split(':', 1)[1]} — skip"
    if c.startswith("cooling-off"):
        return c
    if "probability" in c.lower() or "prob/RR" in c:
        return "Model chance is below the minimum bar"
    if "max loss" in c.lower():
        return "Max loss is too high for this wallet"
    if "reward:risk" in c.lower() or "RR" in c:
        return "Reward after charges is too small versus the risk"
    if "data too old" in c.lower():
        return "Price data is too old — refresh before acting"
    if "outside entry window" in c.lower():
        return "Outside the entry window"
    if c.startswith("watch —"):
        return c.replace("watch —", "Watch —", 1)
    return c


def plain_reasons(codes: list[str] | None, *, limit: int = 3) -> list[str]:
    out = []
    for c in codes or []:
        p = plain_reason(c)
        if p and p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out


def what_you_can_do_today(*, life_status: str, kill_switch: bool, lockout: bool, fno_unlocked: bool, force_paper: bool) -> str:
    if life_status == "dead":
        return "Stop trading. Review your journal — a reset is required."
    if kill_switch:
        return "Kill switch is on — no Ready tickets until you turn it off in Settings."
    if lockout:
        return "Cooling-off lockout is active — sit out until it ends."
    if life_status == "survival":
        return "Survival mode: at most one idea. Prefer sitting out over stacking another loss."
    parts = []
    if force_paper:
        parts.append("Paper / practice only until measured results unlock live.")
    if not fno_unlocked:
        parts.append("Options are practice-only.")
    parts.append("Take only Ready tickets that still fit after Groww charges.")
    return " ".join(parts)


def distance_words(dist: float, label: str) -> str:
    if dist >= 0:
        return f"₹{dist:.0f} above the {label} line"
    return f"₹{abs(dist):.0f} below the {label} line"
