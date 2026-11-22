"""Rank order suggestions by combined risk / profit / duration / wallet score.

Top picks must fit the wallet AND be NET profitable after Groww charges
(brokerage, STT, exchange, GST, stamp, DP). A “buy 400 / sell 410” that
loses money after charges is never ranked.
"""

from __future__ import annotations

from typing import Any

from config import trading_config as cfg
from portfolio.charges import trade_economics


TOP_N = 5  # hard cap; life-mode overrides via rank_for_life


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def economics_for_signal(signal) -> dict[str, Any]:
    """Attach / compute Groww net economics for ranking + UI."""
    is_fno = getattr(signal, "strategy", "") == "fno_intraday" or getattr(signal, "ticket_kind", "") == "fno"
    reasons = getattr(signal, "reasons", None)
    if not isinstance(reasons, dict):
        reasons = {}
    fno = reasons.get("fno") or {}
    groww = getattr(signal, "groww", None) or {}
    if not fno and isinstance(groww, dict):
        fno = groww.get("fno") or {}

    if is_fno:
        if isinstance(fno.get("economics"), dict) and fno["economics"].get("verdict"):
            econ = fno["economics"]
        else:
            buy = float(fno.get("buy_ltp") or getattr(signal, "entry_trigger", 0) or 0)
            sell = float(fno.get("target_ltp") or getattr(signal, "target1", 0) or 0)
            lot = int(fno.get("lot_size") or getattr(signal, "qty", 1) or 1)
            econ = trade_economics(
                buy_price=buy,
                sell_price=sell,
                units=lot,
                product="NRML",
                is_option=True,
                min_net_rs=5.0,
            )
        return econ

    # Normal equity / multi-day CNC
    cached = reasons.get("economics")
    if isinstance(cached, dict) and cached.get("verdict"):
        return cached
    entry = float(getattr(signal, "entry_trigger", 0) or 0)
    t1 = float(getattr(signal, "target1", 0) or 0)
    qty = int(getattr(signal, "qty", 0) or 0)
    product = str(getattr(signal, "product", None) or "CNC")
    if entry <= 0 or t1 <= 0 or qty < 1:
        return {
            "net_ok": False,
            "verdict": "NET_LOSS",
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "charges_total": 0.0,
            "tip": "Incomplete ticket prices",
            "breakeven_sell": entry,
        }
    buy_p, sell_p = (entry, t1) if getattr(signal, "side", "BUY") == "BUY" else (t1, entry)
    return trade_economics(
        buy_price=buy_p,
        sell_price=sell_p,
        units=qty,
        product=product,
        is_option=False,
        min_net_rs=5.0,
    )


def score_signal(signal, wallet: float) -> dict[str, Any]:
    """Return breakdown + total score 0–100 (higher = better suggestion)."""
    wallet = max(float(wallet or 0), 1.0)
    used = float(getattr(signal, "capital_used", 0) or 0)
    max_loss = float(getattr(signal, "max_loss", 0) or 0)
    prob = float(getattr(signal, "probability", 0) or 0)
    rr = float(getattr(signal, "rr_net", 0) or 0)
    entry = float(getattr(signal, "entry_trigger", 0) or 0)
    t1 = float(getattr(signal, "target1", 0) or 0)
    is_fno = getattr(signal, "strategy", "") == "fno_intraday" or getattr(signal, "ticket_kind", "") == "fno"

    reasons = getattr(signal, "reasons", None)
    if not isinstance(reasons, dict):
        reasons = {}
    fno = reasons.get("fno") or {}
    groww = getattr(signal, "groww", None) or {}
    if not fno and isinstance(groww, dict):
        fno = groww.get("fno") or {}

    econ = economics_for_signal(signal)
    signal.economics = econ

    profit_rs = float(econ.get("net_pnl") or 0)
    gross_rs = float(econ.get("gross_pnl") or 0)
    charges = float(econ.get("charges_total") or 0)
    profit_pct = 0.0
    if is_fno:
        debit = float(fno.get("buy_debit_rs") or used or 1)
        loss_rs = float(fno.get("loss_at_sl_rs") or max_loss or 1)
        profit_pct = (profit_rs / debit) * 100 if debit else 0
        if loss_rs > 0 and profit_rs > 0:
            rr = max(rr, profit_rs / loss_rs)
        duration_hrs = 5.0
        duration_label = "same day (~5h)"
    else:
        if entry > 0 and used > 0:
            profit_pct = (profit_rs / used) * 100
        if max_loss > 0 and profit_rs > 0:
            rr = max(rr, profit_rs / (max_loss + charges * 0.5))
        duration_hrs = 72.0  # multi-day CNC swing
        duration_label = "2–5 trading days (CNC)"

    # --- component scores (0–100) ---
    profit_comp = _clamp(profit_pct * 1.2)
    if rr > 0:
        profit_comp = _clamp(0.55 * profit_comp + 0.45 * _clamp(rr * 28))
    if profit_rs < 5:
        profit_comp = min(profit_comp, 15.0)

    loss_pct_wallet = (max_loss / wallet) * 100
    risk_comp = _clamp(100 - loss_pct_wallet * 2.2)
    if max_loss <= 0 or used <= 0:
        risk_comp = 20.0

    edge_comp = _clamp(prob * 100)
    gate = getattr(signal, "gate", None) or {}
    if isinstance(gate, dict):
        if gate.get("result") == "PASS":
            edge_comp = _clamp(edge_comp + 12)
        elif gate.get("result") == "BLOCK":
            edge_comp = _clamp(edge_comp - 18)

    if duration_hrs <= 6:
        duration_comp = 92.0
    elif duration_hrs <= 48:
        duration_comp = 78.0
    elif duration_hrs <= 120:
        duration_comp = 70.0  # multi-day still fine for Normal
    else:
        duration_comp = 45.0

    fit_ratio = used / wallet
    if used > wallet + 1e-6:
        wallet_comp = 0.0
    elif fit_ratio < 0.15:
        wallet_comp = 55.0
    elif fit_ratio <= 0.70:
        wallet_comp = 95.0
    elif fit_ratio <= 1.0:
        wallet_comp = 75.0
    else:
        wallet_comp = 0.0

    pricing = str(fno.get("pricing") or "")
    live_bonus = 0.0
    if is_fno and pricing in {"LIVE_NSE", "LIVE_INDSTOCKS", "GROWW_PASTE"}:
        live_bonus = 5.0
    elif is_fno and pricing in {"ESTIMATE", "NO_LIVE", "NO_LIVE_FIT", ""}:
        live_bonus = -15.0
        edge_comp = _clamp(edge_comp - 10)

    # Consensus / agents rank nudge (never overrides net_ok / capital filters).
    # Prefer consensus score; fall back to legacy agents-only badges.
    consensus_bonus = 0.0
    pipe = getattr(signal, "pipeline", None) or {}
    consensus = pipe.get("consensus") if isinstance(pipe, dict) else None
    if isinstance(consensus, dict) and consensus.get("score") is not None:
        if consensus.get("vetoed"):
            consensus_bonus = -20.0
        else:
            points = float(getattr(cfg, "CONSENSUS_RANK_POINTS", 10.0) or 10.0)
            consensus_bonus = points * float(consensus["score"])
    else:
        agents = pipe.get("agents") if isinstance(pipe, dict) else None
        if not isinstance(agents, dict):
            gate_a = getattr(signal, "gate", None) or {}
            agents = gate_a.get("agents") if isinstance(gate_a, dict) else None
        if isinstance(agents, dict):
            if agents.get("vetoed"):
                consensus_bonus = -20.0
            elif agents.get("neutral"):
                consensus_bonus = -float(getattr(cfg, "AGENTS_RANK_PENALTY_HOLD", 8.0) or 8.0)
            elif agents.get("badge") == "agents_bullish":
                consensus_bonus = float(getattr(cfg, "AGENTS_RANK_BOOST_BUY", 4.0) or 4.0)

    total = (
        0.28 * profit_comp
        + 0.24 * risk_comp
        + 0.22 * edge_comp
        + 0.12 * duration_comp
        + 0.14 * wallet_comp
        + live_bonus
        + consensus_bonus
    )
    total = _clamp(total)

    # Display-only: how many F&O lots the wallet could buy (recommended size stays 1)
    affordable_lots = 0
    if is_fno:
        lot_size = int(fno.get("lot_size") or 0)
        buy_ltp = float(fno.get("buy_ltp") or entry or 0)
        if lot_size > 0 and buy_ltp > 0:
            affordable_lots = int(wallet // (buy_ltp * lot_size))
        elif used > 0:
            affordable_lots = int(wallet // used)

    tip = econ.get("tip") or ""
    if is_fno and affordable_lots > 1:
        tip = (tip + " · " if tip else "") + f"Recommended: 1 lot · wallet can afford {affordable_lots}"

    return {
        "score": round(total, 1),
        "profit_score": round(profit_comp, 1),
        "risk_score": round(risk_comp, 1),
        "edge_score": round(edge_comp, 1),
        "duration_score": round(duration_comp, 1),
        "wallet_score": round(wallet_comp, 1),
        "profit_rs": round(profit_rs, 2),
        "gross_profit_rs": round(gross_rs, 2),
        "charges_rs": round(charges, 2),
        "net_ok": bool(econ.get("net_ok")),
        "verdict": econ.get("verdict"),
        "breakeven_sell": econ.get("breakeven_sell"),
        "tip": tip,
        "profit_pct": round(profit_pct, 1),
        "max_loss_rs": round(max_loss, 2),
        "rr": round(rr, 2),
        "duration": duration_label,
        "capital_used": round(used, 2),
        "wallet_fit_pct": round(fit_ratio * 100, 1),
        "affordable_lots": affordable_lots,
        "recommended_lots": 1 if is_fno else 0,
    }


def _dedupe_key(signal) -> str:
    is_fno = getattr(signal, "strategy", "") == "fno_intraday"
    sym = getattr(getattr(signal, "instrument", None), "symbol", "") or ""
    if is_fno:
        reasons = getattr(signal, "reasons", None) or {}
        fno = reasons.get("fno") if isinstance(reasons, dict) else {}
        groww = getattr(signal, "groww", None) or {}
        if not fno and isinstance(groww, dict):
            fno = groww.get("fno") or {}
        return (
            f"fno:{fno.get('underlying') or sym}:"
            f"{fno.get('option_type')}:{fno.get('strike')}:{fno.get('expiry_label')}"
        )
    return f"eq:{sym}:{getattr(signal, 'side', 'BUY')}"


def _fno_pricing(signal) -> str:
    reasons = getattr(signal, "reasons", None) or {}
    fno = reasons.get("fno") if isinstance(reasons, dict) else {}
    groww = getattr(signal, "groww", None) or {}
    if not fno and isinstance(groww, dict):
        fno = groww.get("fno") or {}
    return str((fno or {}).get("pricing") or "")


def _is_coin_flip(signal) -> bool:
    """Equity TM COIN_FLIP filter. F&O uses local probability — never drop for
    bogus underlying-bar COIN_FLIP (premium levels ≠ index prices)."""
    is_fno = getattr(signal, "strategy", "") == "fno_intraday" or getattr(signal, "ticket_kind", "") == "fno"
    if is_fno:
        # Soft floor: very weak local edge stays off the board
        prob = float(getattr(signal, "probability", 0) or 0)
        return prob > 0 and prob < 0.40
    tm = getattr(signal, "time_machine", None) or {}
    pipe = getattr(signal, "pipeline", None) or {}
    if isinstance(tm, dict) and tm.get("verdict") == "COIN_FLIP":
        return True
    tm2 = pipe.get("time_machine") if isinstance(pipe, dict) else {}
    return isinstance(tm2, dict) and (
        tm2.get("verdict") == "COIN_FLIP" or tm2.get("skip_recommended")
    )


def _is_sticky(signal) -> bool:
    reasons = getattr(signal, "reasons", None)
    return isinstance(reasons, dict) and bool(reasons.get("sticky"))


def mark_sticky_picks(signals: list) -> None:
    """Persist sticky flags on this ranked set; clear sticky on other same-day peers."""
    from django.utils import timezone

    from signals.models import Signal

    keep_ids = {int(s.id) for s in signals if getattr(s, "id", None)}
    today = timezone.localdate()
    for s in signals:
        reasons = dict(s.reasons) if isinstance(s.reasons, dict) else {}
        reasons["sticky"] = True
        reasons["sticky_key"] = _dedupe_key(s)
        s.reasons = reasons
        s.save(update_fields=["reasons"])
    if not keep_ids:
        return
    peers = Signal.objects.filter(
        created_at__date=today,
        status__in=[Signal.Status.NEW, Signal.Status.SKIPPED],
    ).exclude(id__in=keep_ids)
    for peer in peers.iterator():
        reasons = peer.reasons if isinstance(peer.reasons, dict) else {}
        if not reasons.get("sticky"):
            continue
        reasons = dict(reasons)
        reasons.pop("sticky", None)
        peer.reasons = reasons
        peer.save(update_fields=["reasons"])


def rank_signals(signals: list, wallet: float, *, limit: int = TOP_N) -> list:
    """Attach .rank_meta and return top `limit` by score — sticky prior picks kept when still valid.

    Re-scan must refresh levels on the *same* calls, not replace the whole board.
    About half the slots prefer previously sticky ideas that still pass filters;
    remaining slots fill with new higher-scoring ideas.
    """
    scored = []
    seen: set[str] = set()
    for s in signals:
        used = float(getattr(s, "capital_used", 0) or 0)
        if used > float(wallet) + 1e-6:
            continue
        if _is_coin_flip(s):
            continue
        if getattr(s, "strategy", "") == "fno_intraday":
            pricing = _fno_pricing(s).upper()
            if pricing not in {"LIVE_NSE", "LIVE_INDSTOCKS", "GROWW_PASTE"}:
                continue
        key = _dedupe_key(s)
        if key in seen:
            continue
        meta = score_signal(s, wallet)
        if meta["wallet_score"] <= 0:
            continue
        # Never suggest a Groww net-loss target (e.g. buy 400 / sell 410 after fees)
        if not meta.get("net_ok"):
            continue
        # Mild sticky bonus so prior picks don't vanish on tiny score noise
        if _is_sticky(s):
            meta = dict(meta)
            meta["score"] = round(min(100.0, float(meta["score"]) + 4.0), 2)
            meta["sticky"] = True
        seen.add(key)
        s.rank_meta = meta
        s.combo_score = meta["score"]
        scored.append(s)

    scored.sort(key=lambda x: (-float(x.combo_score), -float(getattr(x, "probability", 0) or 0)))
    limit = max(0, int(limit))
    if limit == 0:
        return []

    sticky = [s for s in scored if _is_sticky(s)]
    fresh = [s for s in scored if not _is_sticky(s)]
    sticky.sort(key=lambda x: (-float(x.combo_score), -float(getattr(x, "probability", 0) or 0)))
    fresh.sort(key=lambda x: (-float(x.combo_score), -float(getattr(x, "probability", 0) or 0)))

    # Keep ~half of slots for prior picks when they still clear filters
    keep_n = min(len(sticky), max(1, (limit + 1) // 2)) if sticky else 0
    out: list = []
    used_keys: set[str] = set()
    for s in sticky[:keep_n]:
        k = _dedupe_key(s)
        out.append(s)
        used_keys.add(k)
    for s in fresh:
        if len(out) >= limit:
            break
        k = _dedupe_key(s)
        if k in used_keys:
            continue
        out.append(s)
        used_keys.add(k)
    # Backfill with more sticky if fresh ran short
    if len(out) < limit:
        for s in sticky[keep_n:]:
            if len(out) >= limit:
                break
            k = _dedupe_key(s)
            if k in used_keys:
                continue
            out.append(s)
            used_keys.add(k)
    # Absolute fallback: pure top score
    if not out:
        out = scored[:limit]

    for i, s in enumerate(out, start=1):
        s.rank_place = i
    return out


def life_top_n(life_status: str) -> int:
    if life_status == "dead":
        return 0
    if life_status == "survival":
        return int(getattr(cfg, "SURVIVAL_TOP_N", 1))
    return int(getattr(cfg, "ALIVE_TOP_N", 3))


def no_trade_day_reason(signals: list, *, regime: dict | None = None) -> str | None:
    """Return message when best idea is a coin flip or market is choppy."""
    if regime and regime.get("no_trade_bias"):
        return "Sit out — choppy / high-vol. Do not force a trade and stack another loss"
    tradable = [s for s in signals if not _is_coin_flip(s)]
    if not tradable and signals:
        return "Sit out — best ideas are COIN FLIP. Empty board beats another loss"
    return None


def rank_for_life(
    signals: list,
    wallet: float,
    *,
    life_status: str = "alive",
    regime: dict | None = None,
) -> dict[str, Any]:
    """Life-aware ranking: DEAD=0, SURVIVAL=1, ALIVE=1–3; plus no-trade message."""
    if life_status == "dead":
        return {
            "signals": [],
            "no_trade": "Wallet critical: stop trading — do not force recovery trades",
            "limit": 0,
        }
    limit = life_top_n(life_status)
    # Prefer Normal if F&O lot cannot fit (signals already filtered); still mix sections
    ranked = rank_both_sections(signals, wallet, limit=limit)
    # SURVIVAL: absolute top-1 across both sections
    if life_status == "survival":
        ranked = sorted(
            ranked,
            key=lambda x: (-float(getattr(x, "combo_score", 0) or 0),),
        )[:1]
        for i, s in enumerate(ranked, start=1):
            s.rank_place = i
    msg = no_trade_day_reason(signals, regime=regime)
    if msg and not ranked:
        return {"signals": [], "no_trade": msg, "limit": limit}
    if not ranked and msg:
        return {"signals": [], "no_trade": msg, "limit": limit}
    return {"signals": ranked, "no_trade": None if ranked else msg, "limit": limit}


def rank_both_sections(signals: list, wallet: float, *, limit: int = TOP_N) -> list:
    """Top N Normal + top N F&O (hard cap), life-mode may pass smaller limit."""
    limit = min(int(limit), TOP_N)
    normal = [s for s in signals if getattr(s, "strategy", "") != "fno_intraday"]
    fno = [s for s in signals if getattr(s, "strategy", "") == "fno_intraday"]
    top_n = rank_signals(normal, wallet, limit=limit)
    top_f = rank_signals(fno, wallet, limit=limit)
    for s in top_n:
        s.section = "normal"
    for s in top_f:
        s.section = "fno"
    return (top_n + top_f)[: 2 * limit]


def score_option_row(
    *,
    ltp: float,
    debit: float,
    lot: int,
    wallet: float,
    volume: int = 0,
    oi: int = 0,
    otm: float = 0.0,
    sl_pct: float = 0.35,
    tgt_pct: float = 0.60,
) -> dict[str, Any]:
    """Score a single wallet-fit option strike for Advisor top-5 (net of Groww charges)."""
    wallet = max(float(wallet), 1.0)
    if ltp <= 0 or debit <= 0 or debit > wallet + 1e-6:
        return {"score": 0.0, "skip": True}

    tgt_ltp = round(ltp * (1 + tgt_pct), 2)
    econ = trade_economics(
        buy_price=ltp, sell_price=tgt_ltp, units=int(lot), product="NRML", is_option=True, min_net_rs=5.0
    )
    if not econ.get("net_ok"):
        # try higher targets before skipping
        for bump in (0.75, 0.9, 1.1, 1.4):
            cand = round(ltp * (1 + bump), 2)
            trial = trade_economics(
                buy_price=ltp, sell_price=cand, units=int(lot), product="NRML", is_option=True, min_net_rs=5.0
            )
            if trial.get("net_ok"):
                econ = trial
                tgt_pct = bump
                tgt_ltp = cand
                break
        else:
            return {"score": 0.0, "skip": True, "tip": econ.get("tip"), "verdict": "NET_LOSS"}

    profit_rs = float(econ["net_pnl"])
    loss_rs = debit * sl_pct + float(econ["charges_total"]) * 0.5
    profit_pct = (profit_rs / debit) * 100 if debit else 0
    rr = profit_rs / loss_rs if loss_rs else 0

    profit_comp = _clamp(0.5 * _clamp(profit_pct) + 0.5 * _clamp(rr * 30))
    risk_comp = _clamp(100 - (loss_rs / wallet) * 220)
    liq = _clamp((volume / 5000) * 40 + (oi / 50_000) * 40)
    otm_pen = _clamp(abs(otm) / 500 * 20)
    duration_comp = 92.0
    fit_ratio = debit / wallet
    wallet_comp = 95.0 if 0.2 <= fit_ratio <= 0.75 else (70.0 if fit_ratio <= 1 else 0.0)

    total = (
        0.30 * profit_comp
        + 0.25 * risk_comp
        + 0.15 * liq
        + 0.15 * duration_comp
        + 0.15 * wallet_comp
        - otm_pen
    )
    return {
        "score": round(_clamp(total), 1),
        "profit_score": round(profit_comp, 1),
        "risk_score": round(risk_comp, 1),
        "profit_rs": round(profit_rs, 2),
        "gross_profit_rs": round(float(econ["gross_pnl"]), 2),
        "charges_rs": round(float(econ["charges_total"]), 2),
        "breakeven_sell": econ.get("breakeven_sell"),
        "profit_pct": round(profit_pct, 1),
        "loss_rs": round(loss_rs, 2),
        "rr": round(rr, 2),
        "duration": "same day",
        "target_ltp": tgt_ltp,
        "tip": econ.get("tip") or "",
        "skip": False,
    }
