"""TradeReview helpers — planned vs actual + discipline score."""

from __future__ import annotations

from typing import Any

from config import trading_config as cfg
from portfolio.models import Position, TradeReview


def planned_vs_actual_metrics(pos: Position) -> dict[str, Any]:
    planned_entry = float(pos.planned_entry if pos.planned_entry is not None else pos.entry_price)
    planned_stop = float(pos.planned_stop if pos.planned_stop is not None else pos.stop)
    planned_target = float(pos.planned_target if pos.planned_target is not None else pos.target)
    actual_entry = float(pos.entry_price)
    actual_exit = float(pos.exit_price or 0)
    slip = actual_entry - planned_entry
    risk = abs(planned_entry - planned_stop) or 1e-9
    r_achieved = 0.0
    if pos.side == "BUY":
        r_achieved = (actual_exit - actual_entry) / risk if actual_exit else 0.0
    else:
        r_achieved = (actual_entry - actual_exit) / risk if actual_exit else 0.0
    return {
        "planned_entry": planned_entry,
        "planned_stop": planned_stop,
        "planned_target": planned_target,
        "actual_entry": actual_entry,
        "actual_exit": actual_exit,
        "entry_slippage": round(slip, 4),
        "r_achieved": round(r_achieved, 3),
        "pnl": float(pos.pnl),
    }


def followed_plan(tags: list[str], exit_reason: str) -> bool:
    bad = {
        "entered_early",
        "entered_late",
        "chased",
        "ignored_sl",
        "moved_sl_wider",
        "exited_early",
        "oversized",
        "ignored_news",
        "revenge_trade",
    }
    if any(t in bad for t in tags):
        return False
    return "followed_signal_correctly" in tags or exit_reason in {"target", "sl", "time"}


def create_trade_review(
    pos: Position,
    *,
    exit_reason: str,
    mistake_tags: list[str],
    note: str = "",
    regime_snapshot: dict | None = None,
) -> TradeReview:
    if hasattr(pos, "review"):
        try:
            return pos.review
        except TradeReview.DoesNotExist:
            pass
    tags = [t for t in mistake_tags if t in cfg.MISTAKE_TAGS]
    if not tags:
        raise ValueError("At least one mistake tag (or followed_signal_correctly) is required")
    if exit_reason not in cfg.EXIT_REASONS:
        raise ValueError(f"Invalid exit_reason: {exit_reason}")
    metrics = planned_vs_actual_metrics(pos)
    return TradeReview.objects.create(
        position=pos,
        exit_reason=exit_reason,
        mistake_tags=tags,
        note=note or "",
        planned_vs_actual=metrics,
        followed_plan=followed_plan(tags, exit_reason),
        regime_snapshot=regime_snapshot or {},
    )


def discipline_score(limit: int = 50) -> dict[str, Any]:
    qs = TradeReview.objects.order_by("-created_at")[:limit]
    rows = list(qs)
    if not rows:
        return {"n": 0, "score": None, "warning": "Not enough reviews yet"}
    followed = sum(1 for r in rows if r.followed_plan)
    return {
        "n": len(rows),
        "score": round(100.0 * followed / len(rows), 1),
        "warning": None if len(rows) >= 10 else "Sample size low — treat score cautiously",
    }


def top_mistake_tags(limit: int = 100) -> list[tuple[str, int]]:
    from collections import Counter

    c: Counter[str] = Counter()
    for r in TradeReview.objects.order_by("-created_at")[:limit]:
        for t in r.mistake_tags or []:
            if t != "followed_signal_correctly":
                c[t] += 1
    return c.most_common(5)
