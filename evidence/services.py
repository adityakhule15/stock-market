"""Register and resolve shadow trades."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from config import trading_config as cfg

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def register_from_signal(signal, *, would_recommend: bool | None = None) -> Any:
    from evidence.models import ShadowTrade

    is_fno = getattr(signal, "strategy", "") == "fno_intraday"
    kind = ShadowTrade.Kind.OPTIONS if is_fno else ShadowTrade.Kind.STOCKS
    reasons = signal.reasons if isinstance(signal.reasons, dict) else {}
    fno = reasons.get("fno") if isinstance(reasons.get("fno"), dict) else {}
    gate = signal.gate if isinstance(signal.gate, dict) else {}
    if would_recommend is None:
        would_recommend = gate.get("result") == "PASS" and not (
            is_fno and not _fno_unlocked()
        )
        # Practice: still track options even when locked if they cleared economics
        if is_fno and fno.get("affordable") and fno.get("net_edge_ok"):
            would_recommend = True

    levels = {
        "entry": float(signal.entry_trigger),
        "stop": float(signal.sl_trigger),
        "target": float(signal.target1),
        "qty": int(signal.qty),
    }
    if is_fno:
        levels.update(
            {
                "buy_ltp": float(fno.get("buy_ltp") or signal.entry_trigger or 0),
                "sl_ltp": float(fno.get("sl_ltp") or signal.sl_trigger or 0),
                "target_ltp": float(fno.get("target_ltp") or signal.target1 or 0),
                "lot_size": int(fno.get("lot_size") or 1),
                "option_type": fno.get("option_type"),
                "strike": fno.get("strike"),
                "expiry": fno.get("expiry_label") or fno.get("expiry"),
                "underlying": fno.get("underlying") or signal.instrument.symbol,
            }
        )

    votes = {}
    pipe = signal.pipeline if isinstance(signal.pipeline, dict) else {}
    cons = pipe.get("consensus") or {}
    if isinstance(cons, dict) and cons.get("votes"):
        votes = cons["votes"]

    session = timezone.localdate()
    st, _ = ShadowTrade.objects.update_or_create(
        signal=signal,
        defaults={
            "kind": kind,
            "symbol": signal.instrument.symbol,
            "would_recommend": bool(would_recommend),
            "session_date": session,
            "levels": levels,
            "probability": float(signal.probability) if not is_fno else None,
            "score": float(signal.probability) if is_fno else None,
            "model_votes": votes,
            "meta": {"gate": gate.get("result"), "strategy": signal.strategy},
        },
    )
    return st


def register_signals(signals: list) -> int:
    n = 0
    for s in signals:
        try:
            register_from_signal(s)
            n += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("shadow register failed %s: %s", getattr(s, "id", None), exc)
    return n


def _fno_unlocked() -> bool:
    try:
        from portfolio.strategy_gates import strategy_unlocked

        return strategy_unlocked("fno")
    except Exception:  # noqa: BLE001
        return False


def snapshot_option_marks(underlyings: list[str] | None = None) -> int:
    from evidence.models import ShadowMark, ShadowTrade
    from marketdata.option_chain import get_chain_cached_or_live
    from portfolio.models import PortfolioSettings

    ps = PortfolioSettings.get_solo()
    names = underlyings or list(ps.fno_watchlist or ["NIFTY", "BANKNIFTY"])
    # Also mark open shadow option trades
    open_opts = ShadowTrade.objects.filter(kind="options", outcome="open")
    needed = {(t.levels or {}).get("underlying") or t.symbol for t in open_opts}
    names = list(dict.fromkeys([str(n).upper().replace(".NS", "") for n in names] + list(needed)))
    saved = 0
    for name in names:
        try:
            chain = get_chain_cached_or_live(name, max_age_sec=90)
            expiry = chain.get("expiry") or ""
            for row in chain.get("rows") or []:
                strike = float(row.get("strike") or 0)
                if strike <= 0:
                    continue
                for opt, ltp_k, bid_k, ask_k in (
                    ("CE", "ce_ltp", "ce_bid", "ce_ask"),
                    ("PE", "pe_ltp", "pe_bid", "pe_ask"),
                ):
                    ltp = float(row.get(ltp_k) or 0)
                    if ltp <= 0:
                        continue
                    ShadowMark.objects.create(
                        underlying=name,
                        option_type=opt,
                        strike=strike,
                        expiry=str(expiry),
                        ltp=ltp,
                        bid=float(row.get(bid_k) or 0) or None,
                        ask=float(row.get(ask_k) or 0) or None,
                        source=str(chain.get("source") or ""),
                    )
                    saved += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("option mark %s: %s", name, exc)
    return saved


def resolve_options_shadow(*, force_time_exit: bool = False) -> int:
    """Resolve open options shadow trades using ShadowMark series or 15:10 force."""
    from evidence.models import ShadowMark, ShadowTrade
    from portfolio.charges import trade_economics

    now = timezone.localtime()
    square = now.replace(hour=15, minute=10, second=0, microsecond=0)
    force = force_time_exit or now >= square
    sl_pct = float(getattr(cfg, "FNO_PREMIUM_DROP_EXIT", 0.20))
    tgt_pct = float(getattr(cfg, "FNO_TARGET_PCT", 0.60))
    resolved = 0
    for st in ShadowTrade.objects.filter(kind="options", outcome="open"):
        lv = st.levels or {}
        buy = float(lv.get("buy_ltp") or 0)
        if buy <= 0:
            st.outcome = ShadowTrade.Outcome.NO_DATA
            st.exit_reason = "no_buy_ltp"
            st.resolved_at = timezone.now()
            st.save(update_fields=["outcome", "exit_reason", "resolved_at"])
            resolved += 1
            continue
        sl = float(lv.get("sl_ltp") or buy * (1 - sl_pct))
        tgt = float(lv.get("target_ltp") or buy * (1 + tgt_pct))
        lot = int(lv.get("lot_size") or 1)
        underlying = str(lv.get("underlying") or st.symbol).upper()
        opt = str(lv.get("option_type") or "CE").upper()
        strike = float(lv.get("strike") or 0)
        marks = list(
            ShadowMark.objects.filter(
                underlying=underlying, option_type=opt, strike=strike
            )
            .filter(ts__date=st.session_date)
            .order_by("ts")
        )
        exit_px = None
        reason = None
        for m in marks:
            if m.ltp <= sl:
                exit_px, reason = float(m.ltp), "stop"
                break
            if m.ltp >= tgt:
                exit_px, reason = float(m.ltp), "target"
                break
        if exit_px is None and force:
            if marks:
                exit_px = float(marks[-1].ltp)
                reason = "time"
            else:
                st.outcome = ShadowTrade.Outcome.NO_DATA
                st.exit_reason = "no_marks"
                st.resolved_at = timezone.now()
                st.save(update_fields=["outcome", "exit_reason", "resolved_at"])
                resolved += 1
                continue
        if exit_px is None:
            continue
        eco = trade_economics(
            buy_price=buy, sell_price=exit_px, units=lot, product="NRML", is_option=True, min_net_rs=0.0
        )
        st.gross_pnl = float(eco["gross_pnl"])
        st.charges = float(eco["charges_total"])
        st.net_pnl = float(eco["net_pnl"])
        st.outcome = {
            "target": ShadowTrade.Outcome.TARGET,
            "stop": ShadowTrade.Outcome.STOP,
            "time": ShadowTrade.Outcome.TIME,
        }.get(reason or "time", ShadowTrade.Outcome.TIME)
        st.exit_reason = reason or "time"
        st.resolved_at = timezone.now()
        st.save()
        resolved += 1
    return resolved


def resolve_stocks_shadow() -> int:
    """After EOD bars: simulate each open stock shadow trade."""
    from backtest.simulator import SimLevels, as_dict, simulate_ticket
    from evidence.models import ShadowTrade
    from marketdata.service import MarketDataService

    svc = MarketDataService()
    resolved = 0
    for st in ShadowTrade.objects.filter(kind="stocks", outcome="open"):
        try:
            bars = svc.bars_df(st.symbol, interval="1d")
            if bars is None or len(bars) < 30:
                continue
            lv = st.levels or {}
            # Find signal bar ≈ session_date
            idx = None
            for i, ts in enumerate(bars.index):
                d = ts.date() if hasattr(ts, "date") else None
                if d == st.session_date:
                    idx = i
                    break
            if idx is None:
                idx = len(bars) - 2
            levels = SimLevels(
                entry_trigger=float(lv.get("entry") or 0),
                stop=float(lv.get("stop") or 0),
                target=float(lv.get("target") or 0),
                atr=0.0,
                signal_i=idx,
            )
            if levels.entry_trigger <= 0:
                st.outcome = ShadowTrade.Outcome.NO_DATA
                st.exit_reason = "bad_levels"
                st.resolved_at = timezone.now()
                st.save()
                resolved += 1
                continue
            # Only resolve once horizon has passed
            if len(bars) - 1 < idx + 5:
                continue
            res = simulate_ticket(bars, levels, qty=int(lv.get("qty") or 1), horizon=5)
            st.gross_pnl = res.gross_pnl
            st.charges = res.charges
            st.net_pnl = res.net_pnl
            if not res.triggered:
                st.outcome = ShadowTrade.Outcome.NO_TRIGGER
            elif res.hit_target:
                st.outcome = ShadowTrade.Outcome.TARGET
            elif res.exit_reason == "stop":
                st.outcome = ShadowTrade.Outcome.STOP
            elif res.exit_reason == "adverse":
                st.outcome = ShadowTrade.Outcome.ADVERSE
            else:
                st.outcome = ShadowTrade.Outcome.TIME
            st.exit_reason = res.exit_reason
            st.meta = {**(st.meta or {}), "sim": as_dict(res)}
            st.resolved_at = timezone.now()
            st.save()
            resolved += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("resolve stock shadow %s: %s", st.id, exc)
    return resolved


def maybe_learn_consensus_weights() -> dict | None:
    """After 100 resolved recommended trades, store vote-based weights in models_config."""
    from evidence.models import ShadowTrade
    from evidence.stats import vote_usefulness
    from portfolio.models import PortfolioSettings

    n = ShadowTrade.objects.filter(would_recommend=True).exclude(outcome__in=["open", "no_data"]).count()
    if n < 100:
        return None
    useful = vote_usefulness(lookback=200)
    if not useful:
        return None
    # Map agree_rate → weight, renormalize over known keys
    base = dict(getattr(cfg, "CONSENSUS_WEIGHTS", {}))
    raw = {}
    for k in base:
        u = useful.get(k) or useful.get({"time_machine": "time_machine", "tradingagents": "tradingagents"}.get(k, k))
        if u and u["n"] >= 20:
            raw[k] = max(0.05, float(u["agree_rate"]))
        else:
            raw[k] = float(base[k])
    s = sum(raw.values()) or 1.0
    weights = {k: round(v / s, 4) for k, v in raw.items()}
    ps = PortfolioSettings.get_solo()
    mc = dict(ps.models_config or {})
    mc["consensus_weights"] = weights
    mc["consensus_weights_n"] = n
    mc["consensus_learned_at"] = timezone.now().isoformat()
    ps.models_config = mc
    ps.save(update_fields=["models_config"])
    return weights
