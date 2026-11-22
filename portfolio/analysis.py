"""P&L analysis from confirmed (user-reported) fills."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Sum

from config import trading_config as cfg
from portfolio.models import Position, PortfolioSettings


def analyze_performance(mode: str | None = None) -> dict:
    qs = Position.objects.filter(status=Position.Status.CLOSED)
    if mode:
        qs = qs.filter(mode=mode)
    pnls = list(qs.values_list("pnl", flat=True))
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(float(p) for p in pnls)
    n = len(pnls)
    ps = PortfolioSettings.get_solo()

    today = date.today()
    week_ago = today - timedelta(days=7)
    day_qs = qs.filter(closed_at__date=today)
    week_qs = qs.filter(closed_at__date__gte=week_ago)

    day_pnl = float(day_qs.aggregate(s=Sum("pnl"))["s"] or 0)
    week_pnl = float(week_qs.aggregate(s=Sum("pnl"))["s"] or 0)

    avg_win = float(sum(wins) / len(wins)) if wins else 0.0
    avg_loss = float(sum(losses) / len(losses)) if losses else 0.0
    expectancy = (total / n) if n else 0.0

    # R-multiple approx vs planned max_loss on linked signal when present
    r_list = []
    for pos in qs.select_related("signal")[:500]:
        if pos.signal_id and pos.signal.max_loss:
            risk = float(pos.signal.max_loss)
            if risk > 0:
                r_list.append(float(pos.pnl) / risk)

    return {
        "equity": float(ps.equity),
        "cash": float(ps.cash),
        "life_status": ps.life_status,
        "start_capital": cfg.CAPITAL,
        "dead_floor": cfg.DEAD_FLOOR,
        "total_pnl": total,
        "return_pct": (float(ps.equity) - cfg.CAPITAL) / cfg.CAPITAL * 100,
        "n_closed": n,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "hit_rate": (len(wins) / n) if n else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy_rs": expectancy,
        "avg_R": (sum(r_list) / len(r_list)) if r_list else 0.0,
        "day_pnl": day_pnl,
        "week_pnl": week_pnl,
        "open_count": Position.objects.filter(status=Position.Status.OPEN).count(),
        "verdict": _verdict(total, n, float(ps.equity)),
    }


def _verdict(total_pnl: float, n: int, equity: float) -> str:
    if equity <= cfg.DEAD_FLOOR:
        return "DEAD — stop trading; staff reset required"
    if equity <= cfg.SURVIVAL_EQUITY:
        return "SURVIVAL — stop the bleed; fewer trades, higher bar — do not chase losses"
    if n < 5:
        return "TOO_EARLY — need more confirmed closes for a stable read"
    if total_pnl > 0 and equity > cfg.CAPITAL:
        return "AHEAD — net profitable vs ₹2,000 start"
    if total_pnl < 0:
        return "BEHIND — protect capital; skip revenge trades until edge returns"
    return "FLAT — no edge yet; empty board beats a forced loss"
