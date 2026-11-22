"""Map TradingAgents ratings onto StockScope gate actions.

Hard rules:
- Never turn BLOCK into PASS.
- Never override capital / wallet / net_ok / lockout / kill switch.
"""

from __future__ import annotations

from typing import Any

BUYISH = {"buy", "overweight"}
NEUTRAL = {"hold"}
SELLISH = {"underweight", "sell"}
FAIL_OPEN = {"review", "", "none", "unavailable"}


def normalize_rating(rating: str | None) -> str:
    if rating is None:
        return ""
    return str(rating).strip()


def rating_bucket(rating: str | None) -> str:
    """Return buy | hold | sell | fail_open."""
    r = normalize_rating(rating).lower()
    if r in BUYISH:
        return "buy"
    if r in NEUTRAL:
        return "hold"
    if r in SELLISH:
        return "sell"
    return "fail_open"


def apply_agent_veto(
    *,
    gate_passed: bool,
    gate_reasons: list[str] | None,
    rating: str | None,
) -> dict[str, Any]:
    """Return updated gate fields + UI badge metadata.

    If the original gate did not pass, leave it blocked — agents cannot unblock.
    """
    reasons = list(gate_reasons or [])
    bucket = rating_bucket(rating)
    agents: dict[str, Any] = {
        "rating": normalize_rating(rating),
        "bucket": bucket,
        "badge": None,
        "rank_delta": 0.0,
        "vetoed": False,
        "neutral": False,
        "unavailable": False,
    }

    if not gate_passed:
        agents["badge"] = "agents_skipped_blocked"
        return {
            "passed": False,
            "reasons": reasons,
            "agents": agents,
            "status": "skipped_already_blocked",
        }

    if bucket == "fail_open":
        agents["badge"] = "agents_unavailable"
        agents["unavailable"] = True
        return {
            "passed": True,
            "reasons": reasons,
            "agents": agents,
            "status": "fail_open",
        }

    if bucket == "sell":
        if "agents_veto" not in reasons:
            reasons = ["agents_veto"] + reasons
        agents["badge"] = "agents_veto"
        agents["vetoed"] = True
        return {
            "passed": False,
            "reasons": reasons,
            "agents": agents,
            "status": "veto",
        }

    if bucket == "hold":
        agents["badge"] = "agents_neutral"
        agents["neutral"] = True
        agents["rank_delta"] = -1.0  # ranking applies AGENTS_RANK_PENALTY_HOLD
        return {
            "passed": True,
            "reasons": reasons,
            "agents": agents,
            "status": "neutral",
        }

    # buy / overweight
    agents["badge"] = "agents_bullish"
    agents["rank_delta"] = 1.0
    return {
        "passed": True,
        "reasons": reasons,
        "agents": agents,
        "status": "boost",
    }
