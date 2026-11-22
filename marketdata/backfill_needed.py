"""Decide which symbols need a history backfill (skip if data is already fresh enough)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Max
from django.utils import timezone

from marketdata.models import Bar, Instrument

IST = ZoneInfo("Asia/Kolkata")

# Enough history for features + labels; fresher than this → skip re-download
MIN_DAILY_BARS = 200
MAX_BAR_AGE_DAYS = 5  # calendar days (covers weekends/holidays)


def symbols_needing_backfill(
    symbols: list[str],
    *,
    min_bars: int = MIN_DAILY_BARS,
    max_age_days: int = MAX_BAR_AGE_DAYS,
) -> tuple[list[str], list[str]]:
    """
    Returns (need_backfill, already_ok).
    A symbol needs backfill if: missing, too few 1d bars, or newest bar too old.
    """
    now = timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, IST)
    cutoff = now - timedelta(days=max_age_days)

    need: list[str] = []
    ok: list[str] = []

    # Batch stats
    stats = {
        row["instrument__symbol"]: row
        for row in Bar.objects.filter(interval="1d", instrument__symbol__in=symbols)
        .values("instrument__symbol")
        .annotate(n=Count("id"), latest=Max("ts"))
    }
    existing = set(Instrument.objects.filter(symbol__in=symbols).values_list("symbol", flat=True))

    for sym in symbols:
        if sym not in existing:
            need.append(sym)
            continue
        st = stats.get(sym)
        if not st or st["n"] < min_bars:
            need.append(sym)
            continue
        latest = st["latest"]
        if latest is None:
            need.append(sym)
            continue
        if timezone.is_naive(latest):
            latest = timezone.make_aware(latest, IST)
        if latest < cutoff:
            need.append(sym)
            continue
        ok.append(sym)

    return need, ok


def backfill_status_summary(symbols: list[str]) -> dict:
    need, ok = symbols_needing_backfill(symbols)
    return {
        "need": need,
        "ok": ok,
        "need_count": len(need),
        "ok_count": len(ok),
        "needed": len(need) > 0,
    }
