"""Free-form Advisor AI chat — Gemini / DeepSeek (optional keys) + local fallback.

Signals-only: never places broker orders. Keys from .env:
  GEMINI_API_KEY=...     # free tier @ https://aistudio.google.com/apikey
  DEEPSEEK_API_KEY=...   # https://platform.deepseek.com
  ADVISOR_AI_PROVIDER=auto|gemini|deepseek|local
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

from config import trading_config as cfg
from dashboard.advisor import wallet_now

logger = logging.getLogger(__name__)

SYSTEM = """You are StockScope India Advisor — a personal NSE day/swing trading coach.
Hard rules you must never break:
- StockScope NEVER places broker orders. You only SUGGEST. The user places every order in Groww themselves.
- Capital is about ₹2,000. Respect SURVIVAL (₹1,700) and DEAD (₹800) floors.
- Prefer clear BUY / SKIP / WAIT language. Give entry trigger, SL, target, qty/lots, ₹ at risk, and Groww charges awareness when relevant.
- For F&O debit options only (no selling premium / naked shorts). Square off intraday ideas by 15:10 IST.
- If the edge is a coin flip, say "Best trade is no trade".
- Be concise. Use IST. No US stocks. No averaging down or revenge trades.
- If the user asks you to place/modify/cancel an order via API, refuse.
"""


def provider_status() -> dict[str, Any]:
    gemini = bool((os.getenv("GEMINI_API_KEY") or "").strip())
    deepseek = bool((os.getenv("DEEPSEEK_API_KEY") or "").strip())
    pref = (os.getenv("ADVISOR_AI_PROVIDER") or "auto").strip().lower()
    active = "local"
    if pref == "gemini" and gemini:
        active = "gemini"
    elif pref == "deepseek" and deepseek:
        active = "deepseek"
    elif pref in {"auto", ""}:
        if gemini:
            active = "gemini"
        elif deepseek:
            active = "deepseek"
    elif pref == "local":
        active = "local"
    return {
        "gemini_configured": gemini,
        "deepseek_configured": deepseek,
        "active": active,
        "free_hint": (
            "Add GEMINI_API_KEY (free) from https://aistudio.google.com/apikey "
            "or DEEPSEEK_API_KEY to .env for smarter chat."
            if active == "local"
            else f"Using {active}"
        ),
    }


def _open_positions_blob(limit: int = 8) -> str:
    try:
        from portfolio.live import enrich_position
        from portfolio.models import Position

        rows = []
        for pos in (
            Position.objects.filter(status=Position.Status.OPEN)
            .select_related("instrument", "signal")
            .order_by("-opened_at")[:limit]
        ):
            live = enrich_position(pos)
            sug = (live.get("suggestion") or {}).get("label") or ""
            rows.append(
                f"#{pos.id} {live.get('contract') or pos.instrument.symbol} "
                f"entry ₹{pos.entry_price} LTP ₹{live.get('ltp')} "
                f"SL ₹{pos.stop} Tgt ₹{pos.target} "
                f"P&L ₹{live.get('open_pnl')} · rules={sug}"
            )
        if not rows:
            return "Open positions: none."
        return "Open positions:\n- " + "\n- ".join(rows)
    except Exception as exc:  # noqa: BLE001
        return f"Open positions: unavailable ({exc})"


def _context_blob() -> str:
    w = wallet_now()
    regime = {}
    try:
        from marketdata.regime import detect_regime

        regime = detect_regime()
    except Exception:  # noqa: BLE001
        pass
    events = {}
    try:
        from marketdata.events import event_flags

        events = event_flags()
    except Exception:  # noqa: BLE001
        pass
    return (
        f"Wallet usable ₹{w['wallet']:.0f} (equity ₹{w['equity']:.0f}, cash ₹{w['cash']:.0f}, "
        f"cap ₹{w['hard_cap']:.0f}, life={w['life_status']}). "
        f"Regime: {regime.get('tag', 'unknown')} ({regime.get('reason', '')}). "
        f"Events: {events.get('reason', 'none')}. "
        f"DEAD floor ₹{cfg.DEAD_FLOOR}, SURVIVAL ₹{cfg.SURVIVAL_EQUITY}. "
        + _open_positions_blob()
    )


def _local_reply(message: str, history: list[dict] | None = None) -> str:
    """No-API fallback: keyword coach tied to wallet rules."""
    msg = (message or "").lower()
    w = wallet_now()
    ctx = _context_blob()
    if w["life_status"] == "dead":
        return (
            "Wallet is DEAD — no new suggestions until you reset life in Settings. "
            "Review your Journal first. " + ctx
        )
    if any(x in msg for x in ("place order", "buy for me", "execute", "broker api")):
        return (
            "I won't place orders. StockScope is signals-only — copy levels into Groww yourself. "
            + ctx
        )
    if "nifty" in msg and ("put" in msg or "pe" in msg or "call" in msg or "ce" in msg):
        side = "PUT" if ("put" in msg or " pe" in msg) else "CALL"
        return (
            f"For NIFTY {side}: use the Guided tab to pick a wallet-fit strike (1 lot must fit ₹{w['wallet']:.0f}). "
            "Enter only when the trigger hits; set SL before entry; square off by 15:10 IST. "
            "Open Guided → NIFTY → CALL/PUT → Auto for a concrete ticket. " + ctx
        )
    if any(x in msg for x in ("should i trade", "today", "no trade", "coin flip")):
        return (
            "If Time Machine says COIN FLIP or regime is high-vol, best trade is no trade. "
            "Check Today for ranked ideas; SURVIVAL mode only shows top-1. " + ctx
        )
    if "sl" in msg or "stop" in msg:
        return (
            "Never widen SL. After +1R move SL to breakeven; after +1.5R trail by ATR. "
            "On Positions, use reanalyze then tap “I updated it in Groww”. " + ctx
        )
    if any(
        x in msg
        for x in (
            "for tomorrow",
            "buy tomorrow",
            "purchase tomorrow",
            "what should i buy",
            "what to buy",
            "shopping list",
            "next session",
        )
    ):
        try:
            from dashboard.tomorrow_plan import build_tomorrow_plan

            plan = build_tomorrow_plan(rescan=True)
            lines = [f"**{plan.get('verdict')}** — {plan.get('headline')} ({plan.get('session')})"]
            for i, p in enumerate(plan.get("picks") or [], 1):
                lines.append(
                    f"{i}. {p['title']} ({p['kind']}) · entry ₹{p['entry']} · SL ₹{p['stop']} · "
                    f"Tgt ₹{p['target']} · risk ₹{p['max_loss']}\n   {p['how']}"
                )
            if not plan.get("picks"):
                lines.append("No new buy cleared filters — manage opens / wait.")
            ai = plan.get("ai") or {}
            if ai.get("text"):
                lines.append("\nCoach:\n" + str(ai["text"])[:1200])
            return "\n".join(lines) + "\n\n" + ctx
        except Exception as exc:  # noqa: BLE001
            return f"Tomorrow plan failed ({exc}). Try Scan All on Today first. " + ctx
    if any(x in msg for x in ("hold or sell", "should i sell", "should i hold", "exit my", "square off")):
        try:
            from dashboard.position_advise import advise_all_open

            pack = advise_all_open()
            lines = []
            for row in pack.get("rows") or []:
                if not row.get("ok"):
                    continue
                c = row.get("combined") or {}
                lines.append(
                    f"{row.get('contract')}: **{c.get('verdict')}** "
                    f"(conf {c.get('confidence')}) — {c.get('action')}"
                )
            if lines:
                return "Combined HOLD/SELL (local rules + model; AI coach notes only):\n" + "\n".join(lines) + "\n\n" + ctx
        except Exception:  # noqa: BLE001
            pass
        return "Open Positions → Ask advisor: local rules + model decide HOLD/SELL; Gemini explains only. " + ctx
    return (
        "Ask me about NIFTY/BANKNIFTY CALL/PUT, sizing, SL/target, hold vs sell on opens, or whether to skip today. "
        "Or use the Guided buttons for a full wallet-fit ticket. "
        "Tip: set GEMINI_API_KEY (free) in .env for smarter answers.\n\n" + ctx
    )


# gemini-2.0-flash shut down Jun 2026 — prefer current flash ids, try fallbacks on 404
_GEMINI_MODEL_FALLBACKS = (
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
)


def _gemini_models_to_try() -> list[str]:
    preferred = (os.getenv("GEMINI_MODEL") or "gemini-3.6-flash").strip()
    # Map known retired ids to the recommended replacement
    retired = {
        "gemini-2.0-flash": "gemini-3.6-flash",
        "gemini-2.0-flash-001": "gemini-3.6-flash",
        "gemini-2.0-flash-exp": "gemini-3.6-flash",
        "gemini-1.5-flash": "gemini-3.6-flash",
        "gemini-1.5-flash-latest": "gemini-3.6-flash",
    }
    preferred = retired.get(preferred, preferred)
    out: list[str] = []
    for m in (preferred, *_GEMINI_MODEL_FALLBACKS):
        if m and m not in out:
            out.append(m)
    return out


def _call_gemini(messages: list[dict[str, str]]) -> str:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    # Gemini wants alternating user/model; fold system into first user turn
    contents = []
    preamble = SYSTEM + "\n\nLive context: " + _context_blob()
    first = True
    for m in messages:
        role = "user" if m.get("role") == "user" else "model"
        text = m.get("content") or ""
        if first and role == "user":
            text = preamble + "\n\nUser: " + text
            first = False
        contents.append({"role": role, "parts": [{"text": text}]})
    if first:
        contents.append({"role": "user", "parts": [{"text": preamble + "\n\nSay hello briefly."}]})
    payload = {
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 700},
    }
    # Prefer header so the key is never logged in request URLs / exception text
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    last_err: Exception | None = None
    for model in _gemini_models_to_try():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 404:
                logger.warning("Gemini model %s not found (404) — trying next", model)
                last_err = RuntimeError(f"Gemini model not found: {model}")
                continue
            if r.status_code >= 400:
                # Avoid raising with URL that might echo secrets
                detail = ""
                try:
                    detail = (r.json().get("error") or {}).get("message") or r.text[:180]
                except Exception:  # noqa: BLE001
                    detail = r.text[:180]
                raise RuntimeError(f"Gemini HTTP {r.status_code}: {detail}")
            data = r.json()
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise RuntimeError("Empty Gemini response")
            if model != (os.getenv("GEMINI_MODEL") or "").strip():
                logger.info("Gemini OK via fallback model %s", model)
            return text
        except RuntimeError as exc:
            last_err = exc
            if "not found" in str(exc).lower() or "404" in str(exc):
                continue
            raise
        except requests.RequestException as exc:
            last_err = RuntimeError(f"Gemini network error: {exc.__class__.__name__}")
            raise last_err from exc
    raise RuntimeError(str(last_err or "No Gemini model available"))


def _call_deepseek(messages: list[dict[str, str]]) -> str:
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    model = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    payload_msgs = [{"role": "system", "content": SYSTEM + "\n\nLive context: " + _context_blob()}]
    for m in messages:
        role = m.get("role") if m.get("role") in {"user", "assistant"} else "user"
        payload_msgs.append({"role": role, "content": m.get("content") or ""})
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": payload_msgs, "temperature": 0.4, "max_tokens": 700},
        timeout=45,
    )
    r.raise_for_status()
    data = r.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    text = text.strip()
    if not text:
        raise RuntimeError("Empty DeepSeek response")
    return text


def chat(message: str, history: list[dict] | None = None) -> dict[str, Any]:
    """One free-form turn. history = [{role: user|assistant, content: str}, ...]."""
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "Empty message", "bot": "Type a question first."}
    # Refuse obvious order-placement requests even before calling the model
    if re.search(r"\b(place|execute|submit)\b.{0,40}\border\b", message, re.I):
        return {
            "ok": True,
            "provider": "policy",
            "bot": "I can't place orders. Copy the Groww ticket from Today / Guided advisor and type it yourself.",
            "wallet": wallet_now(),
            "status": provider_status(),
        }

    hist = list(history or [])[-12:]
    hist.append({"role": "user", "content": message})
    status = provider_status()
    active = status["active"]
    bot = ""
    err = None
    try:
        if active == "gemini":
            bot = _call_gemini(hist)
        elif active == "deepseek":
            bot = _call_deepseek(hist)
        else:
            bot = _local_reply(message, hist)
            active = "local"
    except Exception as exc:  # noqa: BLE001
        # Never log raw exception text if it might contain an API key query string
        safe = re.sub(r"(key|api_key|token)=[^&\s]+", r"\1=***", str(exc), flags=re.I)[:180]
        logger.warning("AI chat failed (%s): %s — falling back to local", active, safe)
        err = safe
        bot = _local_reply(message, hist)
        active = "local"

    return {
        "ok": True,
        "provider": active,
        "bot": bot,
        "wallet": wallet_now(),
        "status": status,
        "error": err,
    }
