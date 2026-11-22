"""Hold-or-sell advisory for open positions.

Combines:
  1) Live mark + management rules (SL trail / exit window)
  2) Linked signal / LightGBM probability & plan economics

Gemini (or DeepSeek / local) is coach_notes only — never votes on the verdict.
Never places orders — returns HOLD | SELL | EXIT_PARTIAL with reasons.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from portfolio.live import enrich_position, fno_meta, is_fno
from portfolio.models import Position

logger = logging.getLogger(__name__)


def _ai_vote_enabled() -> bool:
    """When on, Gemini may advise at low weight; rules + local model still dominate."""
    return (os.getenv("ADVISOR_AI_VOTE") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _rules_verdict(live: dict[str, Any], pos: Position) -> dict[str, Any]:
    sug = live.get("suggestion") or {}
    code = str(sug.get("code") or "").upper()
    ltp = live.get("ltp")
    entry = float(pos.entry_price)
    stop = float(pos.stop)
    target = float(pos.target)
    side = (pos.side or "BUY").upper()
    open_pnl = live.get("open_pnl")
    open_pnl_net = live.get("open_pnl_net")

    forced = None
    if ltp is not None:
        m = float(ltp)
        if side == "BUY" and m <= stop:
            forced = "SELL"
        elif side == "BUY" and m >= target:
            forced = "SELL"
        elif side == "SELL" and m >= stop:
            forced = "SELL"
        elif side == "SELL" and m <= target:
            forced = "SELL"

    if forced == "SELL" or code in {"EXIT_NOW", "HIT_STOP", "TAKE_PROFIT", "TAKE_ACTION"}:
        verdict = "SELL"
        weight = 0.9 if forced or code == "EXIT_NOW" else 0.75
        why = sug.get("detail") or sug.get("label") or "Rules: exit condition"
    elif code in {"HOLD", "TRAIL", "MOVE_SL_BREAKEVEN", "RAISE_TARGET", "BOOK_PARTIAL"}:
        verdict = "HOLD" if code != "BOOK_PARTIAL" else "EXIT_PARTIAL"
        weight = 0.65
        why = sug.get("detail") or sug.get("label") or f"Rules: {code}"
    elif code == "WAIT" or ltp is None:
        verdict = "HOLD"
        weight = 0.35
        why = "No reliable live mark yet — default HOLD until LTP is fresh"
    else:
        verdict = "HOLD"
        weight = 0.5
        why = sug.get("detail") or "Rules: between stop and target"

    return {
        "source": "rules",
        "verdict": verdict,
        "weight": weight,
        "code": code,
        "why": why,
        "ltp": ltp,
        "open_pnl": open_pnl,
        "open_pnl_net": open_pnl_net,
        "entry": entry,
        "stop": stop,
        "target": target,
    }


def _model_verdict(pos: Position, live: dict[str, Any]) -> dict[str, Any]:
    from config import trading_config as cfg

    sig = pos.signal if pos.signal_id else None
    prob = float(getattr(sig, "probability", 0) or 0) if sig else 0.0
    tm = getattr(sig, "time_machine", None) if sig else {}
    if not isinstance(tm, dict):
        tm = {}
    tm_verdict = str(tm.get("verdict") or tm.get("label") or "").upper()
    meta = fno_meta(pos) or {}
    econ = meta.get("economics") if isinstance(meta.get("economics"), dict) else {}
    net_ok = econ.get("net_ok")
    ltp = live.get("ltp")
    entry = float(pos.entry_price)
    target = float(pos.target)
    stop = float(pos.stop)
    side = (pos.side or "BUY").upper()
    open_pnl = live.get("open_pnl")
    invested = float(live.get("invested") or 0) or None
    pnl_pct = None
    if open_pnl is not None and invested and invested > 0:
        pnl_pct = 100.0 * float(open_pnl) / invested

    progress = None
    if ltp is not None and abs(target - entry) > 1e-9:
        if side == "BUY":
            progress = (float(ltp) - entry) / (target - entry)
        else:
            progress = (entry - float(ltp)) / (entry - target)

    adverse_prog = float(getattr(cfg, "ADVERSE_PROGRESS_SELL", -0.25))
    fno_pnl_cut = float(getattr(cfg, "FNO_PNL_PCT_SELL", -15.0))

    # Deep drawdown / premium decay — model must vote SELL (rules may already EXIT_NOW)
    if pnl_pct is not None and is_fno(pos) and pnl_pct <= fno_pnl_cut:
        return {
            "source": "model",
            "verdict": "SELL",
            "weight": 0.72,
            "probability": prob,
            "tm": tm_verdict,
            "why": (
                f"Open P&L {pnl_pct:.1f}% of invested (≤ {fno_pnl_cut:.0f}%) — "
                "cut the loser; do not hold for a distant SL"
            ),
            "progress": progress,
            "net_ok": net_ok,
            "pnl_pct": pnl_pct,
        }
    if progress is not None and progress <= adverse_prog:
        return {
            "source": "model",
            "verdict": "SELL",
            "weight": 0.68,
            "probability": prob,
            "tm": tm_verdict,
            "why": (
                f"Progress {progress*100:.0f}% of path to target (≤ {adverse_prog*100:.0f}%) — "
                "edge failed; prefer exit over hope"
            ),
            "progress": progress,
            "net_ok": net_ok,
            "pnl_pct": pnl_pct,
        }

    if tm_verdict in {"COIN_FLIP", "NEGATIVE", "WEAK", "NEUTRAL"} and (
        progress is None or progress < 0.15
    ):
        return {
            "source": "model",
            "verdict": "SELL",
            "weight": 0.58,
            "probability": prob,
            "tm": tm_verdict,
            "why": f"Model/Time Machine {tm_verdict or 'weak'} with no progress — prefer exit",
            "progress": progress,
            "net_ok": net_ok,
            "pnl_pct": pnl_pct,
        }
    if progress is not None and progress >= 0.85:
        return {
            "source": "model",
            "verdict": "SELL",
            "weight": 0.7,
            "probability": prob,
            "tm": tm_verdict,
            "why": f"~{int(progress * 100)}% to target — model says book near target",
            "progress": progress,
            "net_ok": net_ok,
            "pnl_pct": pnl_pct,
        }
    if progress is not None and progress <= 0.05 and prob < 0.52:
        return {
            "source": "model",
            "verdict": "SELL",
            "weight": 0.55,
            "probability": prob,
            "tm": tm_verdict,
            "why": f"Low/mid model prob {prob:.2f} and little/no progress — cut risk",
            "progress": progress,
            "net_ok": net_ok,
            "pnl_pct": pnl_pct,
        }
    if net_ok is False and progress is not None and progress < 0.5:
        return {
            "source": "model",
            "verdict": "HOLD",
            "weight": 0.4,
            "probability": prob,
            "tm": tm_verdict,
            "why": "Plan was thin after Groww charges — only hold if LTP recovers toward breakeven",
            "progress": progress,
            "net_ok": net_ok,
            "pnl_pct": pnl_pct,
        }
    return {
        "source": "model",
        "verdict": "HOLD",
        "weight": 0.55 if prob >= 0.55 else 0.45,
        "probability": prob,
        "tm": tm_verdict,
        "why": f"Model prob {prob:.2f} · TM {tm_verdict or 'n/a'} · still room to target"
        + (f" (~{int(progress * 100)}%)" if progress is not None else ""),
        "progress": progress,
        "net_ok": net_ok,
        "pnl_pct": pnl_pct,
    }


def _briefing(pos: Position, live: dict[str, Any], rules: dict, model: dict) -> str:
    meta = fno_meta(pos) or {}
    contract = live.get("contract") or pos.instrument.symbol
    lines = [
        f"Open position #{pos.id}: {contract} {pos.side} qty={pos.qty} ({live.get('qty_label')}).",
        f"Entry ₹{pos.entry_price} · SL ₹{pos.stop} · Target ₹{pos.target}.",
        f"LTP ₹{live.get('ltp')} · gross open P&L ₹{live.get('open_pnl')} · net≈ ₹{live.get('open_pnl_net')}.",
        f"Invested ₹{live.get('invested')} · current ₹{live.get('current_value')}.",
        f"Rules engine: {rules['verdict']} — {rules['why']}",
        f"Model/TM: {model['verdict']} — {model['why']}",
    ]
    if is_fno(pos):
        lines.append(
            f"F&O {meta.get('option_type')} strike {meta.get('strike')} expiry {meta.get('expiry_label') or meta.get('expiry')} "
            f"lot {meta.get('lot_size')}."
        )
    losing = False
    try:
        if live.get("open_pnl") is not None and float(live.get("open_pnl")) < 0:
            losing = True
        inv = float(live.get("invested") or 0)
        if inv > 0 and live.get("open_pnl") is not None:
            if 100.0 * float(live["open_pnl"]) / inv <= -15:
                losing = True
    except (TypeError, ValueError):
        pass
    if losing:
        lines.append(
            "Position is underwater. First line VERDICT: SELL unless LTP is clearly recovering "
            "toward entry with time left before 15:10. Prefer cutting a loser over hoping for SL. "
            "Never place or claim to place an order."
        )
    else:
        lines.append(
            "Explain the local rules+model verdict in 2–4 short bullets for Groww (SL/target). "
            "First line VERDICT: HOLD or SELL. Never place or claim to place an order."
        )
    return "\n".join(lines)


def _parse_ai_verdict(text: str) -> tuple[str | None, str]:
    raw = (text or "").strip()
    m = re.search(r"VERDICT\s*:\s*(HOLD|SELL|EXIT_PARTIAL)\b", raw, re.I)
    if m:
        return m.group(1).upper().replace("EXIT_PARTIAL", "EXIT_PARTIAL"), raw
    head = raw[:120].upper()
    if re.search(r"\bSELL\b|\bEXIT\b|\bSQUARE\s*OFF\b", head):
        return "SELL", raw
    if re.search(r"\bHOLD\b|\bKEEP\b|\bTRAIL\b", head):
        return "HOLD", raw
    return None, raw


def _ai_coach(briefing: str) -> dict[str, Any]:
    """Optional narrative coach — never used in the weighted verdict by default."""
    from dashboard.ai_chat import chat, provider_status

    status = provider_status()
    prompt = (
        "Advise HOLD or SELL for this open StockScope position. "
        "First line MUST be: VERDICT: HOLD  or VERDICT: SELL  or VERDICT: EXIT_PARTIAL. "
        "Then 2–4 short bullets. Local rules+model still lead; your vote is advisory.\n\n"
        + briefing
    )
    try:
        out = chat(prompt, history=[])
        bot = out.get("bot") or ""
        verdict, _ = _parse_ai_verdict(bot)
        return {
            "source": "ai",
            "provider": out.get("provider") or status.get("active"),
            "verdict": verdict or "HOLD",
            "weight": 0.0,
            "why": bot[:900],
            "raw_ok": bool(verdict),
            "error": out.get("error"),
            "coach_only": True,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("position AI coach failed: %s", exc)
        return {
            "source": "ai",
            "provider": "local",
            "verdict": "HOLD",
            "weight": 0.0,
            "why": f"AI coach unavailable ({exc})",
            "raw_ok": False,
            "error": str(exc)[:160],
            "coach_only": True,
        }


def _combine(rules: dict, model: dict, ai: dict | None = None) -> dict[str, Any]:
    """Verdict = rules + model only. AI is coach_notes unless ADVISOR_AI_VOTE=1."""
    scores = {"HOLD": 0.0, "SELL": 0.0, "EXIT_PARTIAL": 0.0}
    parts = [rules, model]
    if ai and float(ai.get("weight") or 0) > 0:
        parts.append(ai)
    for part in parts:
        v = part.get("verdict") or "HOLD"
        if v not in scores:
            v = "HOLD"
        scores[v] += float(part.get("weight") or 0.4)

    if rules.get("code") in {"EXIT_NOW", "HIT_STOP"} or (
        rules.get("verdict") == "SELL" and float(rules.get("weight") or 0) >= 0.85
    ):
        final = "SELL"
        conf = min(0.95, 0.55 + scores["SELL"] / 2.0)
        tie_break = "rules_force"
    else:
        final = max(scores, key=scores.get)
        total = sum(scores.values()) or 1.0
        conf = round(scores[final] / total, 3)
        tie_break = "rules_model"

    action_map = {
        "HOLD": "Keep the position. Trail SL only tighter if advised — never widen. Watch square-off 15:10 IST for F&O.",
        "SELL": "Square off on Groww now (or at next tick). Record exit LTP on Positions → Square off.",
        "EXIT_PARTIAL": "Book partial profits on Groww; leave a runner with SL at breakeven.",
    }
    return {
        "verdict": final,
        "confidence": conf,
        "tie_break": tie_break,
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "action": action_map.get(final, ""),
        "headline": (
            f"{final} — rules + model"
            + (f" + {ai.get('provider', 'ai')} advice" if ai and float(ai.get("weight") or 0) > 0 else " (AI coach only)")
        ),
    }


def advise_position(pos: Position) -> dict[str, Any]:
    if pos.status != Position.Status.OPEN:
        return {"ok": False, "error": "only open positions can be advised"}
    live = enrich_position(pos)
    rules = _rules_verdict(live, pos)
    model = _model_verdict(pos, live)
    briefing = _briefing(pos, live, rules, model)
    ai = _ai_coach(briefing)
    if _ai_vote_enabled():
        # Take Gemini advice at low weight — never outvotes rules EXIT_NOW / model core
        ai = dict(ai)
        parsed_ok = bool(ai.get("raw_ok"))
        ai["weight"] = 0.28 if parsed_ok else 0.12
        ai["coach_only"] = False
    else:
        ai = dict(ai)
        ai["weight"] = 0.0
        ai["coach_only"] = True
    combined = _combine(rules, model, ai)
    return {
        "ok": True,
        "position_id": pos.id,
        "contract": live.get("contract") or pos.instrument.symbol,
        "live": {
            "ltp": live.get("ltp"),
            "open_pnl": live.get("open_pnl"),
            "open_pnl_net": live.get("open_pnl_net"),
            "suggestion": live.get("suggestion"),
            "qty_label": live.get("qty_label"),
            "invested": live.get("invested"),
            "current_value": live.get("current_value"),
        },
        "rules": rules,
        "model": model,
        "ai": {
            "provider": ai.get("provider"),
            "verdict": ai.get("verdict"),
            "weight": ai.get("weight"),
            "why": ai.get("why"),
            "error": ai.get("error"),
            "coach_only": bool(ai.get("coach_only")),
            "coach_notes": ai.get("why"),
        },
        "combined": combined,
        "briefing": briefing,
    }


def advise_all_open() -> dict[str, Any]:
    qs = (
        Position.objects.filter(status=Position.Status.OPEN)
        .select_related("instrument", "signal")
        .order_by("-opened_at")[:20]
    )
    rows = []
    for pos in qs:
        try:
            rows.append(advise_position(pos))
        except Exception as exc:  # noqa: BLE001
            rows.append({"ok": False, "position_id": pos.id, "error": str(exc)[:160]})
    return {"ok": True, "count": len(rows), "rows": rows}
