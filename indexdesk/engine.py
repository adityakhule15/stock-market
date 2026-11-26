"""Realtime Patterns Desk: detect → score → size calls → persist snapshot."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from config import trading_config as cfg
from features.indicators import atr
from indexdesk.constants import UNDERLYINGS, YF_MAP
from indexdesk.data import live_spot
from indexdesk.features import build_index_live_row
from indexdesk.models import IndexPatternSnapshot
from indexdesk.patterns import bias_from_patterns
from indexdesk.train import score_index_row
from marketdata.service import MarketDataService
from signals.budget import budget_stage
from signals.fno import LOT_SIZE, build_fno_plan
from signals.risk import round_tick

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _next_session_date(as_of: datetime | None = None):
    now = as_of or datetime.now(IST)
    d = now.date()
    # After 15:30 → next session is tomorrow; else still today for intraday
    if now.hour > 15 or (now.hour == 15 and now.minute >= 30):
        d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return d


def _skip_call(reason: str, *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "action": "SKIP",
        "reason": reason,
        "affordable": False,
    }


def _wallet_capital() -> dict[str, Any]:
    try:
        from dashboard.advisor import wallet_now

        return wallet_now()
    except Exception:  # noqa: BLE001
        return {
            "wallet": float(cfg.CAPITAL),
            "equity": float(cfg.CAPITAL),
            "cash": float(cfg.CAPITAL),
            "life_status": "alive",
            "kill_switch": False,
            "lockout_active": False,
        }


def _load_frames(underlying: str) -> tuple[Any, Any, Any]:
    yf_sym = YF_MAP[underlying]
    svc = MarketDataService()
    daily = svc.bars_df(yf_sym, interval="1d")
    try:
        m5 = svc.bars_df(yf_sym, interval="5m", limit=500)
    except Exception:  # noqa: BLE001
        m5 = None
    try:
        m1 = svc.bars_df(yf_sym, interval="1m", limit=500)
    except Exception:  # noqa: BLE001
        m1 = None
    return daily, m5, m1


def _position_advice_for(underlying: str, bias: str, probs: dict[str, Any]) -> dict[str, Any]:
    """HOLD / EXIT / WAIT for open F&O positions on this underlying."""
    try:
        from portfolio.live import enrich_position
        from portfolio.models import Position

        rows = []
        qs = (
            Position.objects.filter(status=Position.Status.OPEN)
            .select_related("instrument", "signal")
            .order_by("-opened_at")[:20]
        )
        for pos in qs:
            live = enrich_position(pos)
            contract = str(live.get("contract") or pos.instrument.symbol or "").upper()
            if underlying not in contract and underlying not in str(pos.instrument.symbol or "").upper():
                # also match via signal reasons
                sig = getattr(pos, "signal", None)
                reasons = (sig.reasons if sig else None) or {}
                und = str((reasons.get("fno") or {}).get("underlying") or "").upper()
                if und != underlying:
                    continue
            sug = live.get("suggestion") or {}
            label = sug.get("label") or sug.get("action") or "HOLD"
            # Bias-aware nudge
            opt = "CE" if "CE" in contract else ("PE" if "PE" in contract else "")
            note = sug.get("reason") or sug.get("note") or ""
            if bias == "BEARISH" and opt == "CE" and label in {"HOLD", "TRAIL", "TRAIL_SL"}:
                label = "EXIT_SOON"
                note = (note + " · Patterns Desk bias turned BEARISH vs your CE.").strip(" ·")
            if bias == "BULLISH" and opt == "PE" and label in {"HOLD", "TRAIL", "TRAIL_SL"}:
                label = "EXIT_SOON"
                note = (note + " · Patterns Desk bias turned BULLISH vs your PE.").strip(" ·")
            # Windows alert when Patterns flips advice to exit
            prev = str(getattr(pos, "last_mgmt_action", "") or "")
            if label in {"EXIT_SOON", "EXIT_NOW", "SELL"} and label != prev:
                try:
                    from notifications.desktop import alert_management

                    alert_management(
                        action=label,
                        reason=note or f"Patterns bias {bias}",
                        symbol=contract or str(pos.instrument.symbol),
                        position_id=pos.id,
                    )
                    pos.last_mgmt_action = label
                    pos.save(update_fields=["last_mgmt_action"])
                except Exception:  # noqa: BLE001
                    pass
            rows.append(
                {
                    "position_id": pos.id,
                    "contract": contract,
                    "entry": float(pos.entry_price),
                    "ltp": live.get("ltp"),
                    "advice": label,
                    "note": note,
                    "prob_up_pct": probs.get("prob_up_pct"),
                    "prob_down_pct": probs.get("prob_down_pct"),
                }
            )
        if not rows:
            return {"open": [], "summary": f"No open {underlying} positions."}
        return {"open": rows, "summary": f"{len(rows)} open {underlying} position(s) reviewed."}
    except Exception as exc:  # noqa: BLE001
        return {"open": [], "summary": f"Position advice unavailable ({exc})"}


def _fno_call_from_plan(
    *,
    code: str,
    bias: str,
    blend: dict[str, Any],
    plan: dict[str, Any],
    wallet: dict[str, Any],
    kind: str,
    hold: str,
    enter_after_ist: str,
    square_off_by_ist: str,
    session_label: str,
) -> dict[str, Any]:
    lots = int(plan.get("lots") or 0)
    lot_size = int(plan.get("lot_size") or LOT_SIZE.get(code) or 0)
    entry = float(plan.get("buy_ltp") or 0)
    stop = float(plan.get("sl_ltp") or 0)
    target = float(plan.get("target_ltp") or 0)
    budget = budget_stage(
        entry=entry,
        stop=stop,
        target=target,
        side="BUY",
        product="NRML",
        is_option=True,
        lot_size=lot_size,
        lots=lots if lots > 0 else None,
        capital=float(wallet.get("wallet") or cfg.CAPITAL),
        equity=float(wallet.get("equity") or cfg.CAPITAL),
    )
    affordable = bool(plan.get("affordable") and budget.get("ok"))
    return {
        "kind": kind,
        "product": "NRML",
        "hold": hold,
        "session_label": session_label,
        "enter_after_ist": enter_after_ist,
        "square_off_by_ist": square_off_by_ist,
        "action": "BUY" if affordable else "SKIP",
        "option_type": plan.get("option_type"),
        "underlying": code,
        "strike": plan.get("strike"),
        "expiry": plan.get("expiry_label") or plan.get("expiry"),
        "buy_ltp": entry,
        "sl_ltp": stop,
        "target_ltp": target,
        "lots": int(budget.get("lots") or lots or 0) if affordable else 0,
        "lot_size": lot_size,
        "affordable_lots": int(budget.get("affordable_lots") or 0),
        "buy_debit_rs": plan.get("buy_debit_rs"),
        "max_loss_rs": plan.get("max_loss_rs") or budget.get("rupees_at_risk"),
        "net_reward": budget.get("net_reward"),
        "net_ok": budget.get("net_ok"),
        "charges_total": budget.get("charges_total"),
        "groww_hint": plan.get("groww_hint") or plan.get("warning"),
        "pricing": plan.get("pricing"),
        "affordable": affordable,
        "reason": plan.get("warning")
        if not affordable
        else (
            f"{bias} · P(up)={blend['prob_up_pct']}% P(down)={blend['prob_down_pct']}% · "
            f"BUY {plan.get('option_type')} {plan.get('strike')} @ ₹{entry} · {session_label}"
        ),
        "wallet": wallet.get("wallet"),
        "life_status": wallet.get("life_status"),
    }


def _build_normal_swing_call(
    *,
    code: str,
    bias: str,
    blend: dict[str, Any],
    spot: float,
    atr_val: float,
    wallet: dict[str, Any],
    next_label: str,
) -> dict[str, Any]:
    """Normal/swing levels on the index + aligned CNC stock ideas from Today."""
    if bias not in {"BULLISH", "BEARISH"} or spot <= 0:
        return _skip_call("neutral / no Normal swing edge", kind="normal")

    bullish = bias == "BULLISH"
    risk = max(atr_val * float(cfg.ATR_SL_MULT), spot * 0.004)
    if bullish:
        entry = round_tick(spot * (1 + float(cfg.ENTRY_BUFFER)), "up")
        stop = round_tick(spot - risk, "down")
        target = round_tick(spot + float(cfg.T1_R) * risk, "up")
        side = "BUY"
    else:
        # Index itself has no CNC short — levels are directional only; stocks use Today puts/skips
        entry = round_tick(spot * (1 - float(cfg.ENTRY_BUFFER)), "down")
        stop = round_tick(spot + risk, "up")
        target = round_tick(spot - float(cfg.T1_R) * risk, "down")
        side = "SELL_BIAS"  # cannot CNC-short index; for Coach / futures context

    # Index has no cash shares — qty is N/A; show levels + linked Normal stocks
    linked: list[dict[str, Any]] = []
    try:
        from dashboard.ranking import rank_for_life
        from signals.models import Signal

        today = timezone.localdate()
        qs = list(
            Signal.objects.filter(
                status__in=[Signal.Status.NEW, Signal.Status.SKIPPED],
                created_at__date=today,
            )
            .select_related("instrument", "model_version")
            .order_by("-created_at")[:80]
        )
        normals = []
        for s in qs:
            reasons = s.reasons if isinstance(s.reasons, dict) else {}
            if reasons.get("fno") or (s.strategy or "") == "fno_intraday":
                continue
            if (s.product or "CNC").upper() not in {"CNC", "DELIVERY", ""}:
                continue
            side_s = (s.side or "BUY").upper()
            if bullish and side_s not in {"BUY", "LONG"}:
                continue
            if (not bullish) and side_s in {"BUY", "LONG"}:
                continue
            normals.append(s)
        life = str(wallet.get("life_status") or "alive").lower()
        ranked = rank_for_life(
            normals,
            float(wallet.get("wallet") or cfg.CAPITAL),
            life_status=life,
        )
        top = list(ranked.get("signals") or [])[:3]
        for s in top:
            linked.append(
                {
                    "signal_id": s.id,
                    "symbol": s.instrument.symbol,
                    "side": s.side,
                    "entry": float(s.entry_trigger or 0),
                    "stop": float(s.sl_trigger or 0),
                    "target": float(s.target1 or 0),
                    "qty": int(s.qty or 0),
                    "prob": float(s.probability or 0),
                    "product": s.product or "CNC",
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("normal linked stocks: %s", exc)

    if not bullish and not linked:
        return {
            "kind": "normal",
            "product": "CNC",
            "hold": "SWING_1_5D",
            "session_label": next_label,
            "action": "SKIP",
            "affordable": False,
            "underlying": code,
            "side": side,
            "spot_entry": float(entry),
            "spot_stop": float(stop),
            "spot_target": float(target),
            "qty": 0,
            "linked_stocks": [],
            "reason": (
                f"{code} bias BEARISH — no CNC short on the index. "
                "Use Next-day PE options, or wait for Normal BUY setups on Today."
            ),
            "wallet": wallet.get("wallet"),
        }

    # For bullish: index levels + optional stock tickets
    budget_note = ""
    qty = 0
    if linked:
        # Prefer first linked stock for wallet sizing display
        first = linked[0]
        budget = budget_stage(
            entry=float(first["entry"]),
            stop=float(first["stop"]),
            target=float(first["target"]),
            side="BUY",
            product="CNC",
            is_option=False,
            capital=float(wallet.get("wallet") or cfg.CAPITAL),
            equity=float(wallet.get("equity") or cfg.CAPITAL),
        )
        qty = int(budget.get("qty") or first.get("qty") or 0)
        budget_note = (
            f"Top Normal: {first['symbol']} entry ₹{first['entry']} SL ₹{first['stop']} "
            f"T1 ₹{first['target']} qty {qty or first.get('qty')}"
        )
        affordable = bool(budget.get("ok") and qty >= 1) or int(first.get("qty") or 0) >= 1
        action = "BUY" if affordable or linked else "WATCH"
    else:
        affordable = False
        action = "WATCH"
        budget_note = (
            f"{code} swing levels ready — no aligned Normal stock ticket on Today yet. "
            "Open Today → Normal after Scan, or use Next-day CE."
        )

    return {
        "kind": "normal",
        "product": "CNC",
        "hold": "SWING_1_5D",
        "session_label": next_label,
        "enter_after_ist": "09:46",
        "square_off_by_ist": "— (swing, not same-day)",
        "action": action,
        "affordable": affordable,
        "underlying": code,
        "side": "BUY" if bullish else side,
        "spot_entry": float(entry),
        "spot_stop": float(stop),
        "spot_target": float(target),
        "atr": round(atr_val, 2),
        "qty": qty,
        "linked_stocks": linked,
        "max_loss_rs": None,
        "reason": (
            f"{bias} · P(up)={blend['prob_up_pct']}% · index levels "
            f"{entry}/{stop}/{target}. {budget_note}"
        ),
        "wallet": wallet.get("wallet"),
        "life_status": wallet.get("life_status"),
        "note": (
            f"{code} is an index — you cannot buy CNC units. "
            "Levels are for direction; trade linked Normal stocks or Options."
        ),
    }


def analyze_underlying(underlying: str, *, persist: bool = True) -> dict[str, Any]:
    """Full Patterns Desk analysis for one index."""
    code = underlying.upper().replace(".NS", "")
    if code not in YF_MAP:
        raise ValueError(f"unsupported underlying {code}")

    wallet = _wallet_capital()
    spot_info = live_spot(code)
    spot = float(spot_info.get("spot") or 0)
    daily, m5, m1 = _load_frames(code)

    X, hits = build_index_live_row(daily, bars_5m=m5, bars_1m=m1, spot=spot or None)
    model_p = score_index_row(X) if X is not None and not X.empty else None
    blend = bias_from_patterns(hits, model_prob_up=model_p)
    bias = blend["bias"]

    atr_val = 0.0
    try:
        if daily is not None and len(daily) >= 20:
            atr_val = float(atr(daily, 14).iloc[-1] or 0)
    except Exception:  # noqa: BLE001
        atr_val = 0.0

    now = timezone.now().astimezone(IST)
    next_d = _next_session_date(now)
    # If still in session, "next day" means the following session after today
    today = now.date()
    if next_d <= today and (now.hour < 15 or (now.hour == 15 and now.minute < 30)):
        next_d = today + timedelta(days=1)
        while next_d.weekday() >= 5:
            next_d += timedelta(days=1)
    next_label = next_d.strftime("%a %d-%b") + " · enter after 09:46 IST"
    same_label = "Today · square off by 15:10 IST"

    life = str(wallet.get("life_status") or "alive").lower()
    blocked = wallet.get("kill_switch") or wallet.get("lockout_active") or life == "dead"
    block_reason = "kill switch / lockout / DEAD wallet — no new tickets"

    call_same = _skip_call(block_reason if blocked else "neutral / no edge", kind="same_day")
    call_next = _skip_call(block_reason if blocked else "neutral / no edge", kind="next_day")
    call_normal = _skip_call(block_reason if blocked else "neutral / no Normal edge", kind="normal")

    if not blocked and bias in {"BULLISH", "BEARISH"} and spot > 0:
        bullish = bias == "BULLISH"
        plan = build_fno_plan(
            code,
            spot,
            bullish=bullish,
            atr=atr_val or spot * 0.01,
            capital=float(wallet.get("wallet") or cfg.CAPITAL),
        )
        call_same = _fno_call_from_plan(
            code=code,
            bias=bias,
            blend=blend,
            plan=plan,
            wallet=wallet,
            kind="same_day",
            hold="SAME_DAY_ONLY",
            enter_after_ist="09:46",
            square_off_by_ist="15:10",
            session_label=same_label,
        )
        # Next-day: same wallet-fit strike idea, held for next session (NRML overnight OK)
        call_next = _fno_call_from_plan(
            code=code,
            bias=bias,
            blend=blend,
            plan=plan,
            wallet=wallet,
            kind="next_day",
            hold="NEXT_SESSION",
            enter_after_ist="09:46",
            square_off_by_ist="15:10 next session",
            session_label=next_label,
        )
        if call_next.get("action") == "BUY":
            call_next["reason"] = (
                f"NEXT DAY · {bias} · BUY {call_next.get('option_type')} "
                f"{call_next.get('strike')} @ ₹{call_next.get('buy_ltp')} · {next_label}"
            )
        call_normal = _build_normal_swing_call(
            code=code,
            bias=bias,
            blend=blend,
            spot=spot,
            atr_val=atr_val or spot * 0.01,
            wallet=wallet,
            next_label=next_label,
        )

    # Prefer showing next-day in hero when after 15:00, else same-day
    primary = call_next if (now.hour > 15 or (now.hour == 15 and now.minute >= 0)) else call_same
    if primary.get("action") != "BUY" and call_same.get("action") == "BUY":
        primary = call_same
    if primary.get("action") not in {"BUY", "WATCH"} and call_normal.get("action") in {"BUY", "WATCH"}:
        primary = call_normal

    pos_adv = _position_advice_for(code, bias, blend)
    payload = {
        "underlying": code,
        "session_date": now.date().isoformat(),
        "next_session": next_d.isoformat(),
        "ts": now.isoformat(),
        "spot": spot,
        "spot_source": spot_info.get("source"),
        "patterns": hits,
        "model_prob_up": model_p,
        "model_prob_up_pct": round(float(model_p) * 100, 1) if model_p is not None else None,
        "bias": bias,
        "prob_up_pct": blend["prob_up_pct"],
        "prob_down_pct": blend["prob_down_pct"],
        "pattern_bull_score": blend["pattern_bull_score"],
        "pattern_bear_score": blend["pattern_bear_score"],
        "call": primary,  # backward compatible
        "call_same_day": call_same,
        "call_next_day": call_next,
        "call_normal": call_normal,
        "position_advice": pos_adv,
        "wallet": wallet,
        "atr": round(atr_val, 2),
        "meta": {
            "n_daily": int(len(daily)) if daily is not None else 0,
            "n_5m": int(len(m5)) if m5 is not None else 0,
            "n_1m": int(len(m1)) if m1 is not None else 0,
        },
    }

    if persist:
        IndexPatternSnapshot.objects.create(
            underlying=code,
            session_date=now.date(),
            spot=spot,
            spot_source=str(spot_info.get("source") or ""),
            patterns=hits,
            model_prob_up=model_p,
            model_prob_down=(1.0 - float(model_p)) if model_p is not None else None,
            bias=bias,
            call={
                "primary": primary,
                "same_day": call_same,
                "next_day": call_next,
                "normal": call_normal,
            },
            position_advice=pos_adv,
            meta={"blend": blend, "bars": payload["meta"]},
        )
        # Keep Today aligned: publish the primary BUY idea as the only index ticket side
        try:
            sync_patterns_idea_to_today(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("patterns→today sync failed for %s: %s", code, exc)
    return payload


def sync_patterns_idea_to_today(payload: dict[str, Any]) -> Any | None:
    """Upsert the Patterns primary F&O idea onto Today so both desks show one side."""
    from marketdata.models import Instrument
    from ml import registry
    from signals.gate import GateResult
    from signals.tasks import _save_signal

    call = payload.get("call") or {}
    if call.get("action") != "BUY" or not call.get("affordable"):
        return None
    if call.get("kind") == "normal":
        return None  # Normal is index levels / linked stocks — not an F&O ticket
    code = str(payload.get("underlying") or "").upper()
    opt = str(call.get("option_type") or "").upper()
    if code not in UNDERLYINGS or opt not in {"CE", "PE"}:
        return None

    champ = registry.champion("swing_lgbm") or registry.champion("index_pattern_lgbm")
    inst, _ = Instrument.objects.get_or_create(symbol=code)
    buy = float(call.get("buy_ltp") or 0)
    sl = float(call.get("sl_ltp") or 0)
    tgt = float(call.get("target_ltp") or 0)
    debit = float(call.get("buy_debit_rs") or 0)
    loss = float(call.get("max_loss_rs") or debit)
    lots = max(1, int(call.get("lots") or 1))
    lot_size = int(call.get("lot_size") or LOT_SIZE.get(code, 65))
    hold = str(call.get("hold") or "SAME_DAY_ONLY")
    kind = str(call.get("kind") or "same_day")
    bias = str(payload.get("bias") or "")
    prob = float(payload.get("prob_up_pct") or 50) / 100.0
    if bias == "BEARISH":
        prob = float(payload.get("prob_down_pct") or 50) / 100.0

    plan = {
        "mode": "FNO_OPTIONS",
        "action": "BUY",
        "underlying": code,
        "symbol": code,
        "option_type": opt,
        "strike": call.get("strike"),
        "expiry_label": call.get("expiry") or call.get("expiry_label") or "",
        "expiry": call.get("expiry") or call.get("expiry_label") or "",
        "expiry_kind": call.get("expiry_kind") or "weekly",
        "lot_size": lot_size,
        "lots": lots,
        "buy_ltp": buy,
        "sl_ltp": sl,
        "target_ltp": tgt,
        "buy_debit_rs": debit,
        "sl_debit_rs": call.get("sl_debit_rs"),
        "target_debit_rs": call.get("target_debit_rs"),
        "max_loss_rs": loss,
        "loss_at_sl_rs": loss,
        "profit_at_tgt_rs": call.get("net_reward") or call.get("profit_at_tgt_rs"),
        "max_premium_per_share": call.get("max_premium_per_share") or buy,
        "max_premium_budget_rs": debit or float(cfg.CAPITAL),
        "affordable": True,
        "product": "NRML",
        "enter_after_ist": call.get("enter_after_ist") or "09:46",
        "square_off_by_ist": call.get("square_off_by_ist") or "15:10",
        "exit_rules": [
            "Confirm live LTP on Groww before buy",
            f"Patterns Desk {bias} — do not buy the opposite side",
            f"Square off by {call.get('square_off_by_ist') or '15:10'} IST",
        ],
        "warning": call.get("groww_hint") or call.get("reason") or "",
        "groww_hint": call.get("groww_hint") or "",
        "pricing": call.get("pricing") or "LIVE",
    }
    order = {
        "side": "BUY",
        "entry_trigger": buy,
        "entry_limit": buy,
        "sl_trigger": sl,
        "sl_limit": sl,
        "target1": tgt,
        "target2": tgt,
        "chase_limit": buy,
        "qty": lots,
        "capital_used": debit,
        "max_loss": loss,
        "rr_net": 1.5,
        "probability": max(0.55, min(0.85, prob)),
        "product": "NRML",
        "strategy": "fno_intraday",
        "symbol": code,
    }
    now = timezone.localtime()
    gate = GateResult(
        passed=False,
        reasons=[
            f"Patterns Desk {bias} idea — place this on Groww from Today only",
            f"{kind.replace('_', ' ')} · {opt} {call.get('strike')}",
            f"max debit ₹{debit:.0f}",
        ],
    )
    return _save_signal(
        inst=inst,
        order=order,
        side="BUY",
        strategy="fno_intraday",
        product="NRML",
        qty=lots,
        capital_used=debit,
        max_loss=loss,
        valid_until=now.replace(hour=15, minute=10, second=0, microsecond=0),
        latest_ts=now,
        source="patterns_desk",
        data_age_min=0,
        prob=float(order["probability"]),
        champ=champ,
        tm={},
        gate=gate,
        pipeline={"budget": {"fno_fits": True}, "patterns_desk": True},
        entry_status="waiting",
        reasons={
            "gates": gate.reasons,
            "prob": order["probability"],
            "kind": "fno",
            "fno": plan,
            "hold": hold,
            "patterns_desk": True,
            "patterns_bias": bias,
            "source_desk": "patterns",
            "call_kind": kind,
            "wallet": (payload.get("wallet") or {}).get("wallet"),
            "max_order_rs": float(cfg.CAPITAL),
        },
        watch_only=True,
    )


def analyze_all(*, persist: bool = True) -> dict[str, Any]:
    rows = {}
    errors = {}
    for name in UNDERLYINGS:
        try:
            rows[name] = analyze_underlying(name, persist=persist)
        except Exception as exc:  # noqa: BLE001
            logger.exception("analyze %s failed", name)
            errors[name] = str(exc)[:200]
    return {
        "ok": True,
        "underlyings": rows,
        "errors": errors,
        "ts": datetime.now(IST).isoformat(),
    }


def latest_snapshot(underlying: str | None = None) -> IndexPatternSnapshot | None:
    qs = IndexPatternSnapshot.objects.all()
    if underlying:
        qs = qs.filter(underlying=underlying.upper())
    return qs.order_by("-ts").first()


def latest_board() -> dict[str, Any]:
    """UI payload: freshest snapshot per underlying, or live analyze if missing."""
    board = {}
    for name in UNDERLYINGS:
        snap = latest_snapshot(name)
        age_sec = None
        if snap is not None:
            age_sec = (timezone.now() - snap.ts).total_seconds()
        if snap is None or (age_sec is not None and age_sec > 300):
            try:
                board[name] = analyze_underlying(name, persist=True)
                continue
            except Exception as exc:  # noqa: BLE001
                if snap is None:
                    board[name] = {"underlying": name, "error": str(exc)[:160], "bias": "NEUTRAL"}
                    continue
        raw_call = snap.call if isinstance(snap.call, dict) else {}
        if "same_day" in raw_call or "next_day" in raw_call or "normal" in raw_call:
            call_same = raw_call.get("same_day") or {}
            call_next = raw_call.get("next_day") or {}
            call_normal = raw_call.get("normal") or {}
            primary = raw_call.get("primary") or call_same or call_next or call_normal
        else:
            # Legacy single-call snapshots
            call_same = raw_call
            call_next = {}
            call_normal = {}
            primary = raw_call
        blend = (snap.meta or {}).get("blend") or {}
        board[name] = {
            "underlying": snap.underlying,
            "session_date": snap.session_date.isoformat(),
            "ts": snap.ts.astimezone(IST).isoformat(),
            "spot": snap.spot,
            "spot_source": snap.spot_source,
            "patterns": snap.patterns,
            "model_prob_up": snap.model_prob_up,
            "model_prob_up_pct": round(float(snap.model_prob_up) * 100, 1)
            if snap.model_prob_up is not None
            else None,
            "bias": snap.bias,
            "prob_up_pct": blend.get("prob_up_pct"),
            "prob_down_pct": blend.get("prob_down_pct"),
            "call": primary,
            "call_same_day": call_same,
            "call_next_day": call_next,
            "call_normal": call_normal,
            "position_advice": snap.position_advice,
            "meta": snap.meta,
            "from_db": True,
            "age_sec": age_sec,
        }
        if board[name]["prob_up_pct"] is None and snap.model_prob_up is not None:
            board[name]["prob_up_pct"] = round(float(snap.model_prob_up) * 100, 1)
            board[name]["prob_down_pct"] = round(100 - float(board[name]["prob_up_pct"]), 1)
    return {"ok": True, "board": board, "ts": datetime.now(IST).isoformat()}
