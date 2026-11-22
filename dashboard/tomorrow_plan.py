"""What to purchase for tomorrow's session.

Uses ranked wallet-fit signals (local LightGBM/ranking + net Groww economics).
Gemini/_ai_coach is narrative only — never changes the pick list.
Signals-only — you place every order in Groww yourself.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

from config import trading_config as cfg
from dashboard.advisor import wallet_now
from dashboard.ranking import life_top_n, mark_sticky_picks, rank_for_life, rank_signals
from portfolio.models import PortfolioSettings, Position

logger = logging.getLogger(__name__)


def _next_session_label() -> str:
    now = timezone.localtime()
    d = now.date()
    # After 15:30 IST → tomorrow; weekend → next weekday
    if now.hour > 15 or (now.hour == 15 and now.minute >= 30):
        d = d + timedelta(days=1)
    while d.weekday() >= 5:  # Sat/Sun
        d = d + timedelta(days=1)
    return d.strftime("%a %d-%b-%Y") + " · enter after 09:46 IST"


def _scan_fresh(*, include_fno: bool = True, max_symbols: int = 12) -> list:
    """Run a light scan so tomorrow plan isn't stale empty.

    Keep this small/fast — Buy for tomorrow must return in the UI quickly.
    """
    from signals.tasks import generate_fno_for_underlyings, generate_signals_for_universe
    from marketdata.management.commands.bootstrap import BOOTSTRAP_SYMBOLS
    from marketdata.universe import shortlist_symbols

    created = []
    warnings: list[str] = []
    try:
        symbols = shortlist_symbols(max_symbols) or BOOTSTRAP_SYMBOLS
        merged = list(dict.fromkeys(list(symbols) + list(BOOTSTRAP_SYMBOLS)))
        created.extend(generate_signals_for_universe(merged[:max_symbols]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("tomorrow normal scan: %s", exc)
        warnings.append(f"stock scan: {exc}")
    if include_fno:
        try:
            ps = PortfolioSettings.get_solo()
            names = [str(x).upper().replace(".NS", "") for x in (ps.fno_watchlist or []) if x]
            # Prefer NIFTY only for speed; BANKNIFTY Yahoo index often fails offline
            names = [n for n in names if n][:2]
            if names:
                created.extend(generate_fno_for_underlyings(names))
        except Exception as exc:  # noqa: BLE001
            logger.warning("tomorrow fno scan: %s", exc)
            warnings.append(f"options scan: {exc}")
    return created


def _candidate_signals(*, rescan: bool = True) -> list:
    from signals.models import Signal

    if rescan:
        _scan_fresh(include_fno=True, max_symbols=12)

    today = timezone.localdate()
    qs = list(
        Signal.objects.filter(status__in=[Signal.Status.NEW, Signal.Status.SKIPPED], created_at__date=today)
        .select_related("instrument", "model_version")
        .order_by("-created_at")[:80]
    )
    if not qs:
        qs = list(
            Signal.objects.filter(status__in=[Signal.Status.NEW, Signal.Status.SKIPPED])
            .select_related("instrument", "model_version")
            .order_by("-created_at")[:40]
        )
    for s in qs:
        s.ticket_kind = "fno" if s.strategy == "fno_intraday" else "normal"
    return qs


def _ticket_row(s) -> dict[str, Any]:
    from dashboard.groww import groww_links_for_signal

    links = groww_links_for_signal(s)
    fno = (links or {}).get("fno") or {}
    meta = getattr(s, "rank_meta", None) or {}
    is_fno = s.strategy == "fno_intraday"
    if is_fno:
        title = (
            f"{fno.get('underlying') or s.instrument.symbol} "
            f"{fno.get('strike')} {fno.get('option_type')}"
        )
        how = (
            f"BUY 1 lot ({fno.get('lot_size')}) on Groww NRML · "
            f"buy LTP≈₹{fno.get('buy_ltp') or s.entry_trigger} · "
            f"SL ₹{fno.get('sl_ltp') or s.sl_trigger} · Tgt ₹{fno.get('target_ltp') or s.target1} · "
            f"square off by 15:10 IST same day"
        )
        hold = "SAME DAY only (tomorrow session)"
    else:
        title = s.instrument.symbol.replace(".NS", "")
        how = (
            f"BUY {s.qty} share(s) CNC · trigger ₹{s.entry_trigger} · "
            f"SL ₹{s.sl_trigger} · T1 ₹{s.target1} · multi-day OK"
        )
        hold = "2–5 trading days (CNC)"
    return {
        "id": s.id,
        "kind": "fno" if is_fno else "normal",
        "title": title.strip(),
        "side": s.side,
        "product": s.product,
        "entry": float(s.entry_trigger),
        "stop": float(s.sl_trigger),
        "target": float(s.target1),
        "qty": int(s.qty),
        "capital_used": float(s.capital_used or 0),
        "max_loss": float(s.max_loss or 0),
        "probability": float(s.probability or 0),
        "score": meta.get("score"),
        "net_pnl": meta.get("profit_rs"),
        "charges_rs": meta.get("charges_rs"),
        "net_ok": meta.get("net_ok"),
        "tip": meta.get("tip") or "",
        "how": how,
        "hold": hold,
        "gate": (s.gate or {}).get("result") if isinstance(s.gate, dict) else None,
        "groww": links,
    }


def _ai_coach(plan: dict[str, Any]) -> dict[str, Any]:
    from dashboard.ai_chat import chat, provider_status

    status = provider_status()
    opens = plan.get("open_positions") or []
    picks = plan.get("picks") or []
    lines = [
        f"Build a BUY-FOR-TOMORROW shopping list for NSE (session {plan.get('session')}).",
        f"Wallet usable ₹{plan['wallet']['wallet']:.0f}, life={plan['wallet']['life_status']}.",
        f"Open positions now: {len(opens)} — do not ignore them.",
    ]
    for o in opens[:5]:
        lines.append(f"- OPEN {o}")
    lines.append("Candidate tickets (already wallet-fit + net after Groww charges):")
    if not picks:
        lines.append("- none cleared filters")
    for i, p in enumerate(picks, 1):
        lines.append(
            f"{i}. {p['kind'].upper()} {p['title']} · entry ₹{p['entry']} SL ₹{p['stop']} "
            f"Tgt ₹{p['target']} · risk ₹{p['max_loss']} · score {p.get('score')} · {p['how']}"
        )
    lines.append(
        "Reply with:\n"
        "1) VERDICT: BUY_TOMORROW or SKIP_TOMORROW\n"
        "2) Ordered list of what to purchase tomorrow (max 2 ideas for ₹2000), with Groww steps\n"
        "3) What to do with current opens (HOLD/SELL one-liners)\n"
        "4) One risk reminder. Never place orders."
    )
    try:
        out = chat("\n".join(lines), history=[])
        return {
            "provider": out.get("provider") or status.get("active"),
            "text": out.get("bot") or "",
            "error": out.get("error"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"provider": "local", "text": f"AI unavailable: {exc}", "error": str(exc)[:160]}


def build_tomorrow_plan(*, rescan: bool = True, use_ai: bool = False) -> dict[str, Any]:
    ps = PortfolioSettings.get_solo()
    w = wallet_now()
    session = _next_session_label()
    warnings: list[str] = []

    open_pos = list(
        Position.objects.filter(status=Position.Status.OPEN)
        .select_related("instrument")
        .order_by("-opened_at")[:10]
    )
    open_labels = [
        f"{p.instrument.symbol} {p.side} entry ₹{p.entry_price} SL ₹{p.stop} Tgt ₹{p.target}"
        for p in open_pos
    ]

    if w["life_status"] == "dead":
        return {
            "ok": True,
            "session": session,
            "wallet": w,
            "picks": [],
            "open_positions": open_labels,
            "verdict": "SKIP_TOMORROW",
            "headline": "Wallet DEAD — no new purchases until reset",
            "ai": {"provider": "policy", "text": "Reset life in Settings before buying anything new."},
        }

    # Cash already locked in opens → only plan what free cash can still buy
    free = float(w["wallet"])
    if open_pos and free < 80:
        return {
            "ok": True,
            "session": session,
            "wallet": w,
            "picks": [],
            "open_positions": open_labels,
            "verdict": "SKIP_TOMORROW",
            "headline": "Cash mostly in open trades — manage opens first; no new buy for tomorrow",
            "ai": {
                "provider": "policy",
                "text": "Use Positions → Ask advisor HOLD/SELL. Free cash too small for a new lot/shares.",
            },
        }

    signals = _candidate_signals(rescan=rescan)
    # Prefer Normal CNC for overnight→tomorrow; allow F&O only as next-session same-day plan
    normal = [s for s in signals if s.strategy != "fno_intraday"]
    fno = [s for s in signals if s.strategy == "fno_intraday"]

    limit = life_top_n(ps.life_status)
    # SURVIVAL: 1 idea; ALIVE: up to 2 for tomorrow shopping list
    buy_limit = 1 if ps.life_status == "survival" else min(2, max(1, limit))

    top_n = rank_signals(normal, free, limit=buy_limit)
    # Only add F&O if Normal empty or ALIVE with spare slot and watchlist analyzed
    if len(top_n) < buy_limit and fno:
        top_f = rank_signals(fno, free, limit=buy_limit - len(top_n))
        top_n = top_n + top_f

    try:
        mark_sticky_picks(top_n)
    except Exception:  # noqa: BLE001
        pass

    picks = [_ticket_row(s) for s in top_n]
    # Drop anything that still exceeds free cash
    picks = [p for p in picks if float(p["capital_used"]) <= free + 1e-6]

    verdict = "BUY_TOMORROW" if picks else "SKIP_TOMORROW"
    headline = (
        f"{len(picks)} idea(s) for {session}"
        if picks
        else f"No wallet-fit net-profit idea for {session}"
    )

    if not picks:
        warnings.append(
            "No wallet-fit idea after charges — at ~₹1720 CNC targets often lose to DP fees; "
            "options stay practice-only until unlock."
        )
    plan = {
        "ok": True,
        "session": session,
        "wallet": w,
        "picks": picks,
        "open_positions": open_labels,
        "verdict": verdict,
        "headline": headline,
        "summary": headline,
        "warnings": warnings,
        "notes": [
            "Prepare levels tonight; place on Groww only after 09:46 IST tomorrow.",
            "F&O ideas are same-day only — square off by 15:10 IST.",
            "CNC Normal can be held multi-day. Never widen SL.",
            f"Hard wallet cap ₹{cfg.CAPITAL:.0f}.",
        ],
    }
    # AI coach is optional — default off so the UI does not hang on Gemini/network
    if use_ai:
        plan["ai"] = _ai_coach(plan)
    else:
        plan["ai"] = {
            "provider": "skipped",
            "text": "Local plan only (AI coach skipped for speed). Picks above are from your model + wallet rules.",
        }
    return plan
