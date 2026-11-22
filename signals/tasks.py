from __future__ import annotations

import logging
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
from django.utils import timezone

from config import trading_config as cfg
from config.trading_config import risk_params_for_equity
from features.builders import build_dataset, feature_matrix
from features.indicators import add_indicators
from marketdata.freshness import SignalBlocked, assert_fresh
from marketdata.models import Instrument
from marketdata.service import MarketDataService
from marketdata.universe import shortlist_symbols
from ml import registry
from ml.train import predict_proba
from portfolio.services import get_portfolio_state
from scanner.patterns import scan_symbol, scanner_stage
from signals.engine import OrderContext, build_order, default_valid_until, session_bounds, trend_ok
from signals.fno import build_fno_plan, fno_local_probability
from signals.gate import risk_gate
from signals.models import Signal
from signals.pipeline import run_pipeline
from signals.risk import estimate_costs
from timemachine.sim import replay, simulate, verdict

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _setup_fn_factory(side: str):
    def setup_fn(hist: pd.DataFrame) -> bool:
        if len(hist) < 60:
            return False
        ind = add_indicators(hist)
        close = float(ind["close"].iloc[-1])
        ema50 = float(ind["ema50"].iloc[-1])
        if pd.isna(ema50):
            return False
        return trend_ok(side, close, ema50)

    return setup_fn


def _levels_fn_factory(side: str):
    def levels_fn(hist: pd.DataFrame):
        ind = add_indicators(hist)
        ctx = OrderContext(
            prev_high=float(hist["high"].iloc[-1]),
            prev_low=float(hist["low"].iloc[-1]),
            atr=float(ind["atr14"].iloc[-1]),
            close=float(hist["close"].iloc[-1]),
            ema50=float(ind["ema50"].iloc[-1]),
        )
        order = build_order(side, ctx, prob=cfg.MIN_PROB, cfg_mod=cfg)
        if order is None:
            s = 1 if side == "BUY" else -1
            entry = float(hist["high" if s == 1 else "low"].iloc[-1])
            stop = entry - s * 1.5 * float(ind["atr14"].iloc[-1])
            target = entry + s * 2 * abs(entry - stop)
            return entry, stop, target
        return order["entry_trigger"], order["sl_trigger"], order["target1"]

    return levels_fn


def _tm_bundle(df, order: dict, side: str) -> dict:
    sim = simulate(
        df,
        order["entry_trigger"],
        order["sl_trigger"],
        order["target1"],
        horizon=5,
        n=cfg.TM_SIMS,
        seed=cfg.TM_SEED,
    )
    rep = replay(df, _setup_fn_factory(side), _levels_fn_factory(side), horizon=5)
    cost_R = 0.0
    if order["max_loss"] > 0:
        costs = estimate_costs(
            order["entry_trigger"] * order["qty"],
            order["target1"] * order["qty"],
            order["product"],
            cfg.FEES,
        )
        cost_R = costs / order["max_loss"]
    tm = {**sim, **verdict(sim, rep, cost_R, cfg), "replay": rep}
    tm["replay_str"] = (
        f"{rep['fires']} fires: {rep['target']} target, {rep['stop']} stop, {rep['neither']} neither"
    )
    return tm


def _fno_opt_from_reasons(reasons: dict | None) -> str:
    if not isinstance(reasons, dict):
        return ""
    fno = reasons.get("fno") if isinstance(reasons.get("fno"), dict) else {}
    return str(fno.get("option_type") or "").upper()


def _find_today_open_signal(inst, *, strategy: str, side: str, option_type: str | None = None) -> Signal | None:
    """Reuse today's NEW/SKIPPED ticket for the same name so re-scan updates levels, not a brand-new card."""
    qs = (
        Signal.objects.filter(
            instrument=inst,
            strategy=strategy,
            side=side,
            created_at__date=timezone.localdate(),
            status__in=[Signal.Status.NEW, Signal.Status.SKIPPED],
        )
        .order_by("-created_at")
    )
    for sig in qs[:20]:
        if option_type:
            if _fno_opt_from_reasons(sig.reasons) != str(option_type).upper():
                continue
        return sig
    return None


def _save_signal(
    *,
    inst,
    order: dict,
    side: str,
    strategy: str,
    product: str,
    qty: int,
    capital_used,
    max_loss,
    valid_until,
    latest_ts,
    source: str,
    data_age_min: int,
    prob: float,
    champ,
    tm: dict,
    gate,
    reasons: dict,
    watch_only: bool,
    pipeline: dict | None = None,
    entry_status: str = "waiting",
) -> Signal:
    """Create or update today's ticket for this instrument (levels refresh on re-scan)."""
    passed = gate.passed and not watch_only
    es = entry_status if entry_status in {"waiting", "triggered", "expired"} else "waiting"
    opt = _fno_opt_from_reasons(reasons) if strategy == "fno_intraday" else None
    existing = _find_today_open_signal(inst, strategy=strategy, side=side, option_type=opt or None)

    # Preserve sticky flag across level updates
    if existing and isinstance(existing.reasons, dict) and existing.reasons.get("sticky"):
        if isinstance(reasons, dict):
            reasons = {**reasons, "sticky": True, "sticky_key": existing.reasons.get("sticky_key")}

    data_as_of = latest_ts if timezone.is_aware(latest_ts) else timezone.make_aware(latest_ts)
    fields = dict(
        product=product,
        data_as_of=data_as_of,
        data_source=source,
        valid_until=valid_until,
        entry_trigger=Decimal(str(order["entry_trigger"])),
        entry_limit=Decimal(str(order["entry_limit"])),
        sl_trigger=Decimal(str(order["sl_trigger"])),
        sl_limit=Decimal(str(order["sl_limit"])),
        target1=Decimal(str(order["target1"])),
        target2=Decimal(str(order["target2"])),
        qty=qty,
        capital_used=Decimal(str(capital_used)),
        max_loss=Decimal(str(max_loss)),
        rr_net=order["rr_net"],
        probability=round(prob, 3),
        model_version=champ,
        chase_limit=Decimal(str(order["chase_limit"])),
        data_age_min=data_age_min,
        time_machine=tm,
        gate=gate.as_dict(),
        reasons=reasons,
        pipeline=pipeline or {},
        entry_status=es,
        status=Signal.Status.NEW if passed else Signal.Status.SKIPPED,
        skip_reason="" if passed else "; ".join(gate.reasons),
    )
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.save()
        return existing

    sig = Signal(instrument=inst, side=side, strategy=strategy, **fields)
    sig.save()
    return sig


def generate_signals_for_universe(symbols: list[str] | None = None) -> list[Signal]:
    pf = get_portfolio_state()
    if pf.life_status == "dead":
        logger.warning("Portfolio DEAD — skip all signal generation")
        return []
    champ = registry.champion("swing_lgbm")
    if champ is None:
        logger.warning("No champion model; skip signal generation")
        return []
    bundle = registry.load(champ)
    svc = MarketDataService()
    symbols = symbols or shortlist_symbols(40)
    risk = risk_params_for_equity(pf.equity)
    created: list[Signal] = []

    nifty_ok = True
    try:
        nifty, _ = svc.history("^NSEI", interval="1d", period="6mo")
        nifty_ind = add_indicators(nifty)
        nifty_ok = float(nifty_ind["close"].iloc[-1]) > float(nifty_ind["ema20"].iloc[-1])
    except Exception:  # noqa: BLE001
        pass

    for symbol in symbols:
        try:
            df = svc.bars_df(symbol, interval="1d")
            if df is None or len(df) < 120:
                try:
                    svc.backfill_symbol(symbol, years=2, interval="1d")
                    df = svc.bars_df(symbol, interval="1d")
                except Exception:  # noqa: BLE001
                    df = None
            if df is None or len(df) < 120:
                continue
            latest_ts = df.index[-1].to_pydatetime()
            source = "yfinance_daily"
            try:
                age = assert_fresh(latest_ts, source, interval="1d")
                data_age_min = int(age.total_seconds() // 60)
            except SignalBlocked as exc:
                # One refresh attempt, then skip if still stale
                logger.info("Refreshing stale daily bars %s: %s", symbol, exc)
                try:
                    svc.backfill_symbol(symbol, years=1, interval="1d")
                    df = svc.bars_df(symbol, interval="1d")
                    if df is None or len(df) < 120:
                        continue
                    latest_ts = df.index[-1].to_pydatetime()
                    age = assert_fresh(latest_ts, source, interval="1d")
                    data_age_min = int(age.total_seconds() // 60)
                except Exception as exc2:  # noqa: BLE001
                    logger.info("Stale data %s: %s", symbol, exc2)
                    continue

            ds = build_dataset(df)
            if ds.empty:
                continue
            X, _ = feature_matrix(ds.iloc[[-1]])
            try:
                prob = float(predict_proba(bundle, X)[0])
            except OSError as exc:
                logger.error(
                    "LightGBM native crash on %s (%s). Retrain with: python manage.py train --mode swing",
                    symbol,
                    exc,
                )
                continue
            row = ds.iloc[-1]
            close = float(row["close"])
            ema50 = float(row["ema50"])
            atr = float(row["atr14"])

            if close > ema50:
                bullish, side = True, "BUY"
            elif close < ema50:
                bullish, side = False, "SELL"
            else:
                continue

            # Wallet = cash you can actually spend (₹2000 book)
            wallet = min(float(pf.equity), float(pf.free_cash), float(cfg.CAPITAL))
            if wallet < 50:
                logger.info("Wallet empty — stop scanning")
                break

            # Anchor high/low to close so Groww price ≈ ticket (fixes stale prev_high gap)
            ctx = OrderContext(
                prev_high=max(float(row["prev_high"]), close),
                prev_low=min(float(row["prev_low"]), close),
                atr=atr,
                close=close,
                ema50=ema50,
            )
            sched = session_bounds()
            # CNC: only BUY sized to wallet (no short / no leverage on ₹2k)
            emit_equity = side == "BUY" and close <= wallet
            order_side = "BUY" if emit_equity else "BUY"
            order = build_order(order_side, ctx, prob, risk=risk, capital=wallet)
            watch_only = False
            if order is None:
                order = build_order(
                    order_side, ctx, prob, risk=risk, capital=wallet, ignore_edge_filters=True
                )
                watch_only = True
            if order is None:
                continue

            order["symbol"] = symbol
            order["atr"] = atr
            inst, _ = Instrument.objects.get_or_create(symbol=symbol)

            # Pattern scanner stage (ORB / flag / oversold) — enrich reasons
            pattern_hits = scan_symbol(symbol, daily=df, nifty_ok=nifty_ok)
            scan_payload = scanner_stage(pattern_hits)
            if pattern_hits:
                best_hit = pattern_hits[0]
                scan_payload["or_high"] = best_hit.get("or_high")
                scan_payload["or_low"] = best_hit.get("or_low")

            # --- Normal equity CNC (BUY only, must fit wallet) ---
            if float(order["capital_used"]) > float(cfg.CAPITAL):
                while int(order["qty"]) > 1 and float(order["entry_trigger"]) * int(order["qty"]) > float(cfg.CAPITAL):
                    order["qty"] = int(order["qty"]) - 1
                order["capital_used"] = round(float(order["entry_trigger"]) * int(order["qty"]), 2)
                order["max_loss"] = round(
                    abs(float(order["entry_trigger"]) - float(order["sl_trigger"])) * int(order["qty"]), 2
                )
            if (
                emit_equity
                and float(order["capital_used"]) <= min(wallet, float(cfg.CAPITAL)) + 1e-9
                and int(order["qty"]) >= 1
            ):
                eq_order = {**order, "strategy": "swing", "product": cfg.PRODUCT, "side": "BUY"}
                side_tm = "BUY" if bullish else "SELL"
                pipe_out = run_pipeline(
                    symbol=symbol,
                    scanner=scan_payload,
                    order=eq_order,
                    pf=pf,
                    tm_bars=df,
                    setup_fn=_setup_fn_factory(side_tm),
                    levels_fn=_levels_fn_factory(side_tm),
                    data_age_min=data_age_min,
                    probability=prob,
                    is_option=False,
                    revenge_symbols=getattr(pf, "revenge_symbols", set()),
                    lockout_until=getattr(pf, "lockout_until", None),
                )
                eq_order = pipe_out["order"]
                tm = pipe_out["time_machine"]
                eq_gate = pipe_out["gate"]
                if watch_only:
                    eq_gate.passed = False
                    if "watch — below min prob/RR" not in eq_gate.reasons:
                        eq_gate.reasons = ["watch — below min prob/RR"] + list(eq_gate.reasons)
                if float(eq_order["capital_used"]) > wallet:
                    eq_gate.passed = False
                    eq_gate.reasons = ["exceeds wallet ₹%.0f" % wallet] + list(eq_gate.reasons)
                data_label = (
                    latest_ts.astimezone(IST).strftime("%d-%b-%Y")
                    if getattr(latest_ts, "tzinfo", None)
                    else str(latest_ts)[:10]
                )
                eq_sig = _save_signal(
                    inst=inst,
                    order=eq_order,
                    side="BUY",
                    strategy="swing",
                    product=cfg.PRODUCT,
                    qty=int(eq_order["qty"]),
                    capital_used=eq_order["capital_used"],
                    max_loss=eq_order["max_loss"],
                    valid_until=sched.get("valid_until_swing") or sched["valid_until"],
                    latest_ts=latest_ts,
                    source=source,
                    data_age_min=data_age_min,
                    prob=prob,
                    champ=champ,
                    tm=tm,
                    gate=eq_gate,
                    pipeline=pipe_out["pipeline"],
                    entry_status=pipe_out.get("entry_status", "waiting"),
                    reasons={
                        "gates": "all_passed" if eq_gate.passed and not watch_only else eq_gate.reasons,
                        "prob": prob,
                        "kind": "normal",
                        "hold": "SWING_MULTI_DAY_OK",
                        "hold_days": sched.get("hold_days_normal", 5),
                        "wallet": wallet,
                        "ref_price": round(close, 2),
                        "data_as_of_label": data_label,
                        "wait_range": sched["wait_range"],
                        "hold_until": sched["hold_until"],
                        "session_label": sched["session_label"],
                        "economics": eq_order.get("economics") or pipe_out.get("budget", {}).get("economics") or {},
                        "gross_pnl": eq_order.get("gross_pnl"),
                        "net_pnl": eq_order.get("net_pnl") or pipe_out.get("budget", {}).get("net_reward"),
                        "charges_total": eq_order.get("charges_total")
                        or pipe_out.get("budget", {}).get("charges_total"),
                        "breakeven_sell": eq_order.get("breakeven_sell")
                        or pipe_out.get("budget", {}).get("breakeven_sell"),
                        "setup": scan_payload.get("setup"),
                    },
                    watch_only=watch_only,
                )
                created.append(eq_sig)
                if eq_gate.passed and not watch_only:
                    pf.open_positions += 1
                    pf.free_cash -= float(eq_order["capital_used"])
                    pf.trades_today += 1
                    wallet = min(float(pf.equity), float(pf.free_cash), float(cfg.CAPITAL))

            # Stock/index F&O is generated only from the F&O tab (user-selected underlying)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Signal gen failed for %s: %s", symbol, exc)

    # Multi-model review (Kronos, analyst, insider, TradingAgents top-N, consensus)
    # runs in a background thread so Scan / premarket never block.
    if created:
        try:
            from signals.model_review import enqueue_model_review

            signal_ids = [s.id for s in created if getattr(s, "id", None)]
            enqueue_model_review(signal_ids)
        except Exception as exc:  # noqa: BLE001
            logger.exception("model review enqueue skipped: %s", exc)

    # F&O is on-demand from the F&O tab (user picks underlying) — not auto-dumped here
    return created


FNO_YF_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
}


def generate_fno_for_underlying(underlying: str, wallet_cap: float | None = None) -> list[Signal]:
    """Analyze one F&O underlying only (CALL + PUT if wallet-fit)."""
    return generate_fno_for_underlyings([underlying], wallet_cap=wallet_cap)


def generate_fno_for_underlyings(
    underlyings: list[str], wallet_cap: float | None = None
) -> list[Signal]:
    """Analyze multiple F&O underlyings (CALL + PUT per name when wallet-fit)."""
    from portfolio.services import get_portfolio_state

    names = []
    for u in underlyings or []:
        name = str(u or "").upper().replace(".NS", "").strip()
        if name and name not in names:
            names.append(name)
    if not names:
        return []
    champ = registry.champion("swing_lgbm")
    if champ is None:
        return []
    svc = MarketDataService()
    pf = get_portfolio_state()
    cap = float(wallet_cap if wallet_cap is not None else cfg.CAPITAL)
    # Do NOT wipe today's tickets — upsert updates levels so re-scan is not 100% new names
    return _fno_option_tickets(svc, pf, champ, underlyings=names, wallet_cap=cap)


def _fno_option_tickets(
    svc,
    pf,
    champ,
    *,
    underlyings: list[str] | None = None,
    wallet_cap: float = cfg.CAPITAL,
) -> list[Signal]:
    """Emit CE/PE plans for selected underlyings; each order ≤ wallet/₹2000."""
    out: list[Signal] = []
    wallet = min(float(pf.equity), float(pf.free_cash), float(wallet_cap), float(cfg.CAPITAL))
    if wallet < 50:
        return out
    # Capital preservation: pause new F&O ideas when equity is damaged
    pause_below = float(getattr(cfg, "FNO_PAUSE_BELOW_EQUITY", 0) or 0)
    if pause_below > 0 and float(pf.equity) < pause_below:
        logger.info(
            "F&O paused — equity ₹%.2f < pause floor ₹%.0f (recover CNC first)",
            float(pf.equity),
            pause_below,
        )
        return out
    fno_min_prob = float(getattr(cfg, "FNO_MIN_PROB", cfg.MIN_PROB))

    names = [u.upper().replace(".NS", "") for u in (underlyings or ["NIFTY", "BANKNIFTY"])]
    for name in names:
        yf_sym = FNO_YF_MAP.get(name) or f"{name}.NS"
        try:
            df = svc.bars_df(yf_sym, interval="1d")
            if df is None or len(df) < 60:
                # Fallback history fetch
                df, _ = svc.history(yf_sym, interval="1d", period="6mo")
            if df is None or len(df) < 60:
                logger.info("No data for %s", name)
                continue
            ind = add_indicators(df)
            spot = float(ind["close"].iloc[-1])
            atr = float(ind["atr14"].iloc[-1])
            bullish = spot > float(ind["ema50"].iloc[-1])
            now_ist = timezone.localtime()
            inst, _ = Instrument.objects.get_or_create(symbol=name)
            try:
                from marketdata.regime import detect_regime

                regime = detect_regime()
            except Exception:  # noqa: BLE001
                regime = {"tag": "range"}
            # Always offer CALL + PUT; user picks. Debit hard-capped at ₹2000.
            for opt in ("CE", "PE"):
                plan = build_fno_plan(
                    name, spot, bullish=(opt == "CE"), atr=atr, capital=wallet, option_type=opt
                )
                if not plan.get("affordable"):
                    continue
                debit = min(
                    float(plan.get("buy_debit_rs") or plan["max_premium_budget_rs"]),
                    float(cfg.CAPITAL),
                    wallet,
                )
                if debit <= 0 or debit > float(cfg.CAPITAL) + 1e-9:
                    continue
                loss_rs = min(float(plan.get("loss_at_sl_rs") or plan["max_loss_rs"]), debit, float(cfg.CAPITAL))
                local_prob = fno_local_probability(
                    option_type=opt,
                    bullish=bullish,
                    spot=spot,
                    strike=float(plan.get("strike") or spot),
                    atr=atr,
                    plan=plan,
                    regime=regime,
                )
                if float(local_prob) < fno_min_prob:
                    logger.info(
                        "Skip F&O %s %s — local_prob %.2f < FNO_MIN_PROB %.2f",
                        name,
                        opt,
                        float(local_prob),
                        fno_min_prob,
                    )
                    continue
                order = {
                    "side": "BUY",
                    "entry_trigger": float(plan.get("buy_ltp") or 0),
                    "entry_limit": float(plan.get("buy_ltp") or 0),
                    "sl_trigger": float(plan.get("sl_ltp") or 0),
                    "sl_limit": float(plan.get("sl_ltp") or 0),
                    "target1": float(plan.get("target_ltp") or 0),
                    "target2": float(plan.get("target_ltp") or 0),
                    "chase_limit": float(plan.get("max_premium_per_share") or 0),
                    "qty": 1,
                    "capital_used": debit,
                    "max_loss": loss_rs,
                    "rr_net": 1.5,
                    "probability": local_prob,
                    "product": "NRML",
                    "strategy": "fno_intraday",
                    "symbol": name,
                    "atr": atr,
                }
                lot_size = int(plan.get("lot_size") or 1)
                scan_payload = {
                    "ok": True,
                    "setup": "fno_debit",
                    "reason": f"{name} {opt} debit plan",
                    "hits": [{"setup": "fno_debit"}],
                }
                pipe_out = run_pipeline(
                    symbol=name,
                    scanner=scan_payload,
                    order=order,
                    pf=pf,
                    tm_bars=df,
                    data_age_min=0,
                    probability=float(order["probability"]),
                    is_option=True,
                    lot_size=lot_size,
                    lots=1,
                    option_type=opt,
                    strike=plan.get("strike"),
                    expiry=plan.get("expiry_label"),
                    revenge_symbols=getattr(pf, "revenge_symbols", set()),
                    lockout_until=getattr(pf, "lockout_until", None),
                )
                order = pipe_out["order"]
                tm = pipe_out["time_machine"]
                gate = pipe_out["gate"]
                # F&O remains watch_only — user confirms live premium on Groww
                gate.passed = False
                preferred = (bullish and opt == "CE") or ((not bullish) and opt == "PE")
                gate.reasons = [
                    f"watch — {name} {'CALL' if opt == 'CE' else 'PUT'} "
                    f"(max debit ₹{debit:.0f} ≤ ₹2000; confirm live premium on Groww)"
                ] + (["bias matches"] if preferred else ["counter-bias — smaller size / skip if unsure"]) + list(
                    gate.reasons
                )
                if not plan.get("affordable"):
                    pipe_out["pipeline"].setdefault("budget", {})["fno_fits"] = False
                    pipe_out["pipeline"]["budget"]["reason"] = "no F&O lot fits the wallet — show Normal ideas only"
                out.append(
                    _save_signal(
                        inst=inst,
                        order=order,
                        side="BUY",
                        strategy="fno_intraday",
                        product="NRML",
                        qty=1,
                        capital_used=debit,
                        max_loss=order["max_loss"],
                        valid_until=now_ist.replace(hour=15, minute=10, second=0, microsecond=0),
                        latest_ts=df.index[-1].to_pydatetime(),
                        source="yfinance",
                        data_age_min=0,
                        prob=order["probability"],
                        champ=champ,
                        tm=tm,
                        gate=gate,
                        pipeline=pipe_out["pipeline"],
                        entry_status=pipe_out.get("entry_status", "waiting"),
                        reasons={
                            "gates": gate.reasons,
                            "prob": order["probability"],
                            "kind": "fno",
                            "fno": plan,
                            "hold": "SAME_DAY_ONLY",
                            "wallet": wallet,
                            "max_order_rs": float(cfg.CAPITAL),
                            "ref_price": round(spot, 2),
                            "wait_range": session_bounds()["wait_range_fno"],
                            "hold_until": session_bounds()["hold_until_fno"],
                            "data_as_of_label": session_bounds()["session_label"],
                        },
                        watch_only=True,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.info("Index F&O %s skipped: %s", name, exc)

    # Model review + agents on UNDERLYING only (direction/risk context).
    # Background so F&O scan never blocks. Consensus never sizes F&O lots.
    if out:
        try:
            from research.adapter import agents_enabled
            from research.workers import enqueue_callable
            from signals.model_review import enqueue_model_review

            signal_ids = [s.id for s in out if getattr(s, "id", None)]
            enqueue_model_review(signal_ids)

            if agents_enabled():
                def _review_fno():
                    from signals.models import Signal
                    from research.services import attach_underlying_research

                    qs = list(Signal.objects.filter(id__in=signal_ids))
                    if qs:
                        attach_underlying_research(qs)

                enqueue_callable("agents-review-fno", _review_fno)
        except Exception as exc:  # noqa: BLE001
            logger.exception("F&O model review enqueue skipped: %s", exc)
    return out
