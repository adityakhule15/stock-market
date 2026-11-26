"""Patterns Desk snapshot text for Coach / Gemini prompts."""

from __future__ import annotations

from typing import Any


def _fmt_call(call: dict[str, Any] | None) -> str:
    if not call:
        return "no call"
    kind = call.get("kind") or "call"
    if call.get("kind") == "normal":
        linked = call.get("linked_stocks") or []
        top = linked[0] if linked else None
        base = (
            f"Normal/swing {call.get('action')} index "
            f"{call.get('spot_entry')}/{call.get('spot_stop')}/{call.get('spot_target')}"
        )
        if top:
            base += f" · stock {top.get('symbol')} qty {top.get('qty')}"
        if call.get("action") == "SKIP":
            return f"SKIP Normal ({call.get('reason') or 'no fit'})"
        return base
    if call.get("action") != "BUY" or not call.get("affordable"):
        return f"SKIP {kind} ({call.get('reason') or 'no fit'})"
    return (
        f"{kind}: BUY {call.get('option_type')} {call.get('strike')} "
        f"@ ₹{call.get('buy_ltp')} × {call.get('lots')} lot "
        f"(debit ₹{call.get('buy_debit_rs')}, SL ₹{call.get('sl_ltp')}, "
        f"TGT ₹{call.get('target_ltp')}, hold={call.get('hold')}, "
        f"session={call.get('session_label')})"
    )


def _fmt_patterns(patterns: list | None, limit: int = 5) -> str:
    if not patterns:
        return "none"
    bits = []
    for p in patterns[:limit]:
        bits.append(
            f"{p.get('name')} {p.get('side')} conf={round(float(p.get('confidence') or 0)*100)}%"
        )
    return "; ".join(bits)


def _fmt_positions(advice: dict[str, Any] | None) -> str:
    if not advice:
        return "none"
    rows = advice.get("open") or []
    if not rows:
        return advice.get("summary") or "none"
    return " | ".join(
        f"#{r.get('position_id')} {r.get('contract')} → {r.get('advice')}"
        for r in rows[:6]
    )


def patterns_context_blob(*, refresh_if_stale: bool = False) -> str:
    """Compact DB-backed context for hold/sell Coach answers."""
    try:
        from indexdesk.constants import UNDERLYINGS
        from indexdesk.engine import analyze_underlying, latest_snapshot
        from django.utils import timezone

        lines = ["Patterns Desk (NIFTY/BANKNIFTY):"]
        for name in UNDERLYINGS:
            snap = latest_snapshot(name)
            age = (timezone.now() - snap.ts).total_seconds() if snap else None
            if refresh_if_stale and (snap is None or (age is not None and age > 600)):
                try:
                    row = analyze_underlying(name, persist=True)
                except Exception as exc:  # noqa: BLE001
                    lines.append(f"- {name}: unavailable ({exc})")
                    continue
            elif snap is None:
                lines.append(f"- {name}: no snapshot yet — open Patterns tab or wait for session poll.")
                continue
            else:
                blend = (snap.meta or {}).get("blend") or {}
                raw = snap.call if isinstance(snap.call, dict) else {}
                row = {
                    "spot": snap.spot,
                    "spot_source": snap.spot_source,
                    "bias": snap.bias,
                    "prob_up_pct": blend.get("prob_up_pct"),
                    "prob_down_pct": blend.get("prob_down_pct"),
                    "model_prob_up": snap.model_prob_up,
                    "patterns": snap.patterns,
                    "call": raw.get("primary") or raw,
                    "call_same_day": raw.get("same_day") or {},
                    "call_next_day": raw.get("next_day") or {},
                    "call_normal": raw.get("normal") or {},
                    "position_advice": snap.position_advice,
                }
            p_up = row.get("prob_up_pct")
            p_dn = row.get("prob_down_pct")
            if p_up is None and row.get("model_prob_up") is not None:
                p_up = round(float(row["model_prob_up"]) * 100, 1)
                p_dn = round(100 - float(p_up), 1)
            lines.append(
                f"- {name}: spot {row.get('spot')} ({row.get('spot_source')}), "
                f"bias {row.get('bias')}, P(up)={p_up}% P(down)={p_dn}%, "
                f"patterns=[{_fmt_patterns(row.get('patterns'))}], "
                f"same_day={_fmt_call(row.get('call_same_day') or row.get('call'))}, "
                f"next_day={_fmt_call(row.get('call_next_day'))}, "
                f"normal={_fmt_call(row.get('call_normal'))}, "
                f"positions={_fmt_positions(row.get('position_advice'))}"
            )
        lines.append(
            "When user asks hold/sell: use position_advice + bias + probabilities above; "
            "never place orders; respect wallet/life floors."
        )
        return " ".join(lines) if len(lines) == 1 else "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Patterns Desk: unavailable ({exc})"
