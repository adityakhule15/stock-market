"""Nine-stage day-trader pipeline → Signal.pipeline JSON."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from config import trading_config as cfg
from signals.budget import budget_stage
from signals.entry_timer import evaluate_entry
from signals.exit_planner import plan_exits
from signals.gate import GateResult, PortfolioState, risk_gate
from signals.news import is_fno_product, news_for_symbol
from signals.ticket import build_groww_ticket
from timemachine.sim import replay, simulate, verdict

IST = ZoneInfo("Asia/Kolkata")

PIPELINE_KEYS = (
    "scanner",
    "news",
    "entry",
    "exit",
    "time_machine",
    "budget",
    "gate",
    "ticket",
    "alerts",
    "analyst",
    "insider",
    "skill_checklist",
)


def empty_pipeline() -> dict[str, Any]:
    return {k: {} for k in PIPELINE_KEYS}


def run_pipeline(
    *,
    symbol: str,
    scanner: dict[str, Any],
    order: dict[str, Any],
    pf: PortfolioState,
    tm_bars: pd.DataFrame | None = None,
    intraday_bars: pd.DataFrame | None = None,
    setup_fn=None,
    levels_fn=None,
    data_age_min: int = 0,
    probability: float = 0.5,
    is_option: bool = False,
    lot_size: int = 1,
    lots: int | None = None,
    option_type: str | None = None,
    strike: float | None = None,
    expiry: str | None = None,
    regime: dict[str, Any] | None = None,
    revenge_symbols: set[str] | None = None,
    lockout_until: datetime | None = None,
    cfg_mod=cfg,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run stages and return {pipeline, gate, time_machine, order_updates, blocked}."""
    now = now or datetime.now(IST)
    pipe = empty_pipeline()
    product = order.get("product", "CNC")
    strategy = order.get("strategy", "swing")
    side = order.get("side", "BUY")
    intraday = strategy in {"intraday", "fno_intraday"} or product == "MIS"

    # 1 Scanner
    pipe["scanner"] = {
        "ok": bool(scanner.get("ok", True)),
        "setup": scanner.get("setup"),
        "hits": scanner.get("hits", []),
        "reason": scanner.get("reason", ""),
    }

    # 2 News
    news = news_for_symbol(
        symbol,
        product="NRML" if is_option or is_fno_product(product, strategy) else product,
        cfg_mod=cfg_mod,
    )
    if is_option or is_fno_product(product, strategy):
        if news.get("action") == "warn":
            news = {**news, "action": "block", "ok": False, "reason": news["reason"].replace("caution", "block")}
    pipe["news"] = news

    # 3 Entry timer
    entry = evaluate_entry(
        entry_trigger=float(order["entry_trigger"]),
        side=side,
        bars=intraday_bars,
        now=now,
    )
    pipe["entry"] = entry

    # 4 Exit planner — equity/ORB only. Never rewrite F&O premium SL/target with
    # underlying ATR (that yields nonsense negative stops and false COIN_FLIP).
    if is_option:
        exit_plan = {
            "ok": True,
            "sl_trigger": float(order["sl_trigger"]),
            "sl_limit": float(order.get("sl_limit") or order["sl_trigger"]),
            "target1": float(order["target1"]),
            "target2": float(order.get("target2") or order["target1"]),
            "stop_method": "fno_premium",
            "reason": "F&O debit plan levels kept (premium SL/target) · square off by 15:10 IST",
            "square_off_by": getattr(cfg_mod, "SQUARE_OFF_BY", "15:10"),
        }
        pipe["exit"] = exit_plan
    else:
        exit_plan = plan_exits(
            entry=float(order["entry_trigger"]),
            side=side,
            or_high=scanner.get("or_high") or order.get("or_high"),
            or_low=scanner.get("or_low") or order.get("or_low"),
            atr=order.get("atr"),
            intraday=intraday,
            cfg_mod=cfg_mod,
        )
        if exit_plan.get("ok"):
            order = {
                **order,
                "sl_trigger": exit_plan["sl_trigger"],
                "sl_limit": exit_plan["sl_limit"],
                "target1": exit_plan.get("target1", order.get("target1")),
                "target2": exit_plan.get("target2", order.get("target2")),
            }
        pipe["exit"] = exit_plan

    # 5 Time Machine — underlying candle bootstrap is for CNC/equity only.
    # Option premiums ≠ index levels; local F&O score is unproven (not EDGE).
    tm: dict[str, Any]
    if is_option:
        p = float(probability or 0)
        if p >= 0.58:
            label = "UNPROVEN"
        elif p >= 0.48:
            label = "NEUTRAL"
        else:
            label = "WEAK"
        tm = {
            "verdict": label,
            "label": f"{label} — options score {p:.2f} (not a measured probability)",
            "skip_recommended": label == "WEAK",
            "p_target": max(0.0, min(0.85, p)),
            "p_stop": max(0.0, min(0.85, 1.0 - p)),
            "p_neither": 0.0,
            "expected_R": round((p - (1.0 - p)) * 1.5, 3),
            "n": 0,
            "mode": "fno_local",
            "score": p,
            "unproven": True,
            "p_target_pct": round(100 * max(0.0, min(0.85, p)), 1),
            "p_stop_pct": round(100 * max(0.0, min(0.85, 1.0 - p)), 1),
            "p_neither_pct": 0.0,
        }
    elif tm_bars is not None and len(tm_bars) >= 30:
        sim = simulate(
            tm_bars,
            float(order["entry_trigger"]),
            float(order["sl_trigger"]),
            float(order["target1"]),
            horizon=5,
            n=cfg_mod.TM_SIMS,
            seed=cfg_mod.TM_SEED,
        )
        rep = {"fires": 0, "target": 0, "stop": 0, "neither": 0}
        if setup_fn and levels_fn:
            rep = replay(tm_bars, setup_fn, levels_fn, horizon=5)
        cost_R = 0.0
        max_loss = float(order.get("max_loss") or 0)
        if max_loss > 0:
            try:
                from portfolio.charges import trade_economics

                eco = trade_economics(
                    buy_price=float(order["entry_trigger"]),
                    sell_price=float(order["target1"]),
                    units=int(order.get("qty") or 1),
                    product=str(order.get("product") or "CNC"),
                    is_option=False,
                    min_net_rs=0.0,
                )
                cost_R = float(eco["charges_total"]) / max_loss
            except Exception:  # noqa: BLE001
                cost_R = 0.0
        tm = {**sim, **verdict(sim, rep, cost_R, cfg_mod), "replay": rep, "n": int(sim.get("n", cfg_mod.TM_SIMS))}
        label = tm.get("verdict", "UNKNOWN")
        if label == "COIN_FLIP":
            tm["label"] = "COIN FLIP — skip"
            tm["skip_recommended"] = True
        else:
            tm["label"] = label
            tm["skip_recommended"] = label != "EDGE"
        tm["p_target_pct"] = round(100 * float(tm.get("p_target", 0)), 1)
        tm["p_stop_pct"] = round(100 * float(tm.get("p_stop", 0)), 1)
        tm["p_neither_pct"] = round(100 * float(tm.get("p_neither", 0)), 1)
        tm["cost_R"] = round(cost_R, 3)
    else:
        tm = {
            "verdict": "UNKNOWN",
            "label": "UNKNOWN — insufficient bars",
            "skip_recommended": False,
            "p_target": 0,
            "p_stop": 0,
            "p_neither": 0,
            "n": 0,
        }
    pipe["time_machine"] = {
        "ok": tm.get("verdict") == "EDGE",
        "verdict": tm.get("verdict"),
        "label": tm.get("label"),
        "p_target_pct": tm.get("p_target_pct", 0),
        "p_stop_pct": tm.get("p_stop_pct", 0),
        "p_neither_pct": tm.get("p_neither_pct", 0),
        "n": tm.get("n", 0),
        "skip_recommended": tm.get("skip_recommended", False),
        "reason": tm.get("label", ""),
    }

    # 6 Budget
    bud = budget_stage(
        entry=float(order["entry_trigger"]),
        stop=float(order["sl_trigger"]),
        target=float(order["target1"]),
        side=side,
        product=product,
        is_option=is_option,
        lot_size=lot_size,
        lots=lots if lots is not None else order.get("qty") if is_option else None,
        capital=pf.free_cash,
        risk_pct=pf.risk_per_trade,
        max_position_pct=pf.max_position_pct,
        equity=pf.equity,
        cfg_mod=cfg_mod,
    )
    pipe["budget"] = bud
    if bud.get("ok"):
        order = {
            **order,
            "qty": bud["qty"],
            "capital_used": bud["capital_used"],
            "max_loss": bud["max_loss"],
            "rr_net": bud.get("rr_net", order.get("rr_net", 0)),
        }

    order["probability"] = probability
    order["symbol"] = symbol

    # 7 Risk gate (+ lockout / revenge / regime / news block)
    gate = risk_gate(order, pf, tm, data_age_min, cfg_mod=cfg_mod, now=now)
    extra: list[str] = []
    if lockout_until and now < lockout_until:
        extra.append(f"cooling-off lockout until {lockout_until.astimezone(IST).strftime('%Y-%m-%d %H:%M')} IST")
    bare = symbol.replace(".NS", "").replace(".BO", "").upper()
    if revenge_symbols and bare in {s.upper().replace(".NS", "") for s in revenge_symbols}:
        extra.append("revenge block: same symbol hit SL today")
    if news.get("action") == "block":
        extra.append(news.get("reason", "news block"))
    if regime and regime.get("tag") == "high_vol" and intraday:
        extra.append("regime_high_vol")
    if tm.get("verdict") == "COIN_FLIP" and not is_option:
        extra.append("coin_flip")
    if is_option and tm.get("skip_recommended"):
        extra.append("fno_score_weak")
    if not bud.get("ok"):
        extra.append(bud.get("reason", "budget_failed") or "budget_failed")
    # NIFTY regime: skip new stock longs when index below falling EMA50
    if not is_option and side == "BUY":
        try:
            from features.market import nifty_long_ok

            nifty_gate = nifty_long_ok(cfg_mod)
            pipe["regime_nifty"] = nifty_gate
            if not nifty_gate.get("ok", True):
                extra.append("nifty_bear_skip_longs")
        except Exception:  # noqa: BLE001
            pass
    if extra:
        gate = GateResult(False, list(gate.reasons) + extra)
    pipe["gate"] = {
        **gate.as_dict(),
        "ok": gate.passed,
        "reason": "; ".join(gate.reasons) if gate.reasons else "PASS",
    }

    # 8 Ticket (copy gated by checklist in UI)
    ticket = build_groww_ticket(
        symbol=symbol,
        side=side,
        product=product,
        entry_trigger=float(order["entry_trigger"]),
        entry_limit=float(order.get("entry_limit", order["entry_trigger"])),
        sl_trigger=float(order["sl_trigger"]),
        target1=float(order["target1"]),
        qty=int(order.get("qty", 0)),
        lots=int(bud.get("lots") or (order.get("qty") if is_option else 0) or 0),
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        square_off_by=exit_plan.get("square_off_by") if intraday else None,
        checklist_complete=False,
    )
    pipe["ticket"] = ticket

    # 9 Alerts plan (fired later by jobs / live hub)
    pipe["alerts"] = {
        "ok": True,
        "watch_sl": float(order["sl_trigger"]),
        "watch_target": float(order["target1"]),
        "square_off_by": exit_plan.get("square_off_by") if intraday else None,
        "t_minus_min": cfg_mod.SQUARE_OFF_WARN_MIN,
        "channels": ["browser", "telegram"],
        "reason": "alerts armed after you confirm the order",
    }

    # 10–11 Analyst + India insider (filled by background model_review; placeholders here)
    pipe.setdefault("analyst", {"ok": False, "pending": True, "facts_only": True, "vote": None})
    pipe.setdefault("insider", {"ok": False, "pending": True, "flag": "none", "vote": None})

    from signals.skill_checklist import build_skill_checklist

    pipe["skill_checklist"] = build_skill_checklist(pipe, gate_passed=gate.passed)

    return {
        "pipeline": pipe,
        "gate": gate,
        "time_machine": tm,
        "order": order,
        "budget": bud,
        "entry_status": entry.get("status", "WAITING").lower(),
    }
