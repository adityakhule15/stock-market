"""News / results-date stage — NSE announcements + corporate calendar."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from config import trading_config as cfg


def news_for_symbol(
    symbol: str,
    *,
    product: str = "CNC",
    as_of: date | None = None,
    cfg_mod=cfg,
) -> dict[str, Any]:
    """Headline + results-within-N-days check. Fail-open with empty headline if fetch fails."""
    as_of = as_of or date.today()
    bare = symbol.replace(".NS", "").replace(".BO", "").upper()
    headline = ""
    results_date: date | None = None
    try:
        from marketdata.events import announcements_for, next_results_date

        anns = announcements_for(bare, lookback_days=3)
        if anns:
            headline = str(anns[0].get("desc") or anns[0].get("subject") or anns[0].get("headline") or "")[:200]
        results_date = next_results_date(bare, as_of=as_of)
    except Exception:  # noqa: BLE001
        headline = ""
        results_date = None

    days_to_results = None
    action = "ok"
    reason = "no results date nearby"
    if results_date is not None:
        days_to_results = (results_date - as_of).days
        if 0 <= days_to_results <= cfg_mod.RESULTS_WARN_DAYS:
            if product.upper() in {"MIS", "NRML"} or "fno" in product.lower() or product == "NRML":
                # F&O / intraday: block around results
                action = "block"
                reason = f"results within {days_to_results} day(s) — skip F&O/CNC intraday"
            else:
                action = "warn"
                reason = f"results within {days_to_results} day(s) — caution for CNC"

    # Heuristic: product NRML or strategy fno → treat as F&O
    return {
        "ok": action != "block",
        "action": action,
        "headline": headline or "No recent NSE headline",
        "results_date": results_date.isoformat() if results_date else None,
        "days_to_results": days_to_results,
        "reason": reason,
        "symbol": bare,
    }


def is_fno_product(product: str, strategy: str = "") -> bool:
    p = (product or "").upper()
    s = (strategy or "").lower()
    return p in {"NRML", "MIS"} or "fno" in s
