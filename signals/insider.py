"""India insider stage (trader-pelosi NSE adaptation) for the call panel."""

from __future__ import annotations

from datetime import date
from typing import Any

from marketdata.insider import summarize_insider


def insider_for_symbol(
    symbol: str,
    *,
    as_of: date | None = None,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """Fail-open insider summary for pipeline / consensus."""
    try:
        summary = summarize_insider(
            symbol, lookback_days=lookback_days, as_of=as_of
        )
        return {
            "ok": True,
            "unavailable": False,
            **summary,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "unavailable": True,
            "flag": "none",
            "vote": None,
            "label": "insider unavailable",
            "reason": str(exc)[:200],
            "net_value_inr": 0.0,
            "pledge_events": 0,
        }
