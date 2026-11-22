"""One plain-language state per signal card."""

from __future__ import annotations

from typing import Any

from portfolio.strategy_gates import strategy_unlocked, unlock_progress


def card_state_for_signal(signal, *, entry_status: str | None = None) -> dict[str, Any]:
    """Return {state, label, primary_action, show_copy, show_confirm, show_practice, reason}."""
    es = (entry_status or getattr(signal, "entry_status", "") or "").lower()
    gate = signal.gate if isinstance(signal.gate, dict) else {}
    passed = gate.get("result") == "PASS"
    is_fno = getattr(signal, "strategy", "") == "fno_intraday"
    summary = getattr(signal, "gate_summary", "") or ""

    if es == "expired":
        return {
            "state": "expired",
            "label": "Expired",
            "primary_action": None,
            "show_copy": False,
            "show_confirm": False,
            "show_practice": False,
            "reason": summary or "Entry window closed",
        }

    if is_fno and not strategy_unlocked("fno"):
        # Affordable + live priced → practice
        reasons = signal.reasons if isinstance(signal.reasons, dict) else {}
        fno = reasons.get("fno") if isinstance(reasons.get("fno"), dict) else {}
        affordable = bool(fno.get("affordable") or fno.get("net_edge_ok"))
        if affordable or passed:
            prog = unlock_progress("fno")
            return {
                "state": "practice",
                "label": "Practice only",
                "primary_action": "practice",
                "show_copy": False,
                "show_confirm": False,
                "show_practice": True,
                "reason": prog.get("reason")
                or "Options are practice-only until measured results prove they make money after charges",
                "unlock": prog,
            }
        return {
            "state": "skip",
            "label": "Skip",
            "primary_action": None,
            "show_copy": False,
            "show_confirm": False,
            "show_practice": False,
            "reason": summary or "Does not fit wallet or no live price",
        }

    if passed:
        return {
            "state": "ready",
            "label": "Ready",
            "primary_action": "copy",
            "show_copy": True,
            "show_confirm": True,
            "show_practice": False,
            "reason": "",
        }

    # Soft watch (plan for next session / below bar)
    soft = summary in {"plan for next session", ""} or "watch" in (signal.skip_reason or "").lower()
    if soft and not summary:
        summary = "Watch — below the bar for a Ready ticket"
    return {
        "state": "watch" if soft else "skip",
        "label": "Watch" if soft else "Skip",
        "primary_action": None,
        "show_copy": False,
        "show_confirm": False,
        "show_practice": False,
        "reason": summary or "; ".join(gate.get("blocked_by") or [])[:160],
    }
