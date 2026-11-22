"""Strategy unlock gates — practice-first until shadow evidence proves edge.

Phase 1 stub: options (fno) are locked. Stocks stay unlocked for Ready/Watch
cards once the risk gate passes. Phase 3 replaces the fno path with measured
shadow-book stats.
"""

from __future__ import annotations

from typing import Any

from config import trading_config as cfg


def strategy_unlocked(kind: str) -> bool:
    """Return True when real (non-practice) recommendations are allowed.

    kind: "swing" | "stocks" | "fno" | "options"
    """
    k = (kind or "").lower().strip()
    if k in {"fno", "options", "fno_intraday", "option"}:
        try:
            from evidence.stats import fno_unlock_status

            return bool(fno_unlock_status().get("unlocked"))
        except Exception:  # noqa: BLE001
            # Evidence app / tables not ready yet — practice only
            return False
    if k in {"swing", "stocks", "equity", "cnc", "normal"}:
        try:
            from evidence.stats import stocks_unlock_status

            return bool(stocks_unlock_status().get("unlocked"))
        except Exception:  # noqa: BLE001
            # Until shadow book exists, allow stock Ready cards (gate still applies)
            return True
    return False


def unlock_progress(kind: str) -> dict[str, Any]:
    """Progress toward unlock for UI (count / needed / unlocked)."""
    k = (kind or "").lower().strip()
    if k in {"fno", "options", "fno_intraday", "option"}:
        try:
            from evidence.stats import fno_unlock_status

            return fno_unlock_status()
        except Exception:  # noqa: BLE001
            return {
                "unlocked": False,
                "n": 0,
                "needed": int(getattr(cfg, "FNO_UNLOCK_MIN_TRADES", 40)),
                "sessions": 0,
                "sessions_needed": int(getattr(cfg, "FNO_UNLOCK_MIN_SESSIONS", 15)),
                "avg_net": None,
                "ci_lo": None,
                "reason": "practice only — no measured results yet",
            }
    try:
        from evidence.stats import stocks_unlock_status

        return stocks_unlock_status()
    except Exception:  # noqa: BLE001
        return {
            "unlocked": True,
            "n": 0,
            "needed": int(getattr(cfg, "STOCKS_UNLOCK_MIN_TRADES", 60)),
            "sessions": 0,
            "sessions_needed": int(getattr(cfg, "STOCKS_UNLOCK_MIN_SESSIONS", 15)),
            "avg_net": None,
            "ci_lo": None,
            "reason": "stocks unlocked pending shadow book",
        }
