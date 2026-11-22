from __future__ import annotations

from datetime import date
from decimal import Decimal

from config import trading_config as cfg
from marketdata.models import Instrument
from portfolio.models import EquitySnapshot, OrderFill, PortfolioSettings, Position
from signals.gate import PortfolioState
from signals.models import Signal


def refresh_life_status(ps: PortfolioSettings | None = None, *, alert: bool = True) -> PortfolioSettings:
    """Update life_status from equity. DEAD forces kill_switch and cannot auto-clear."""
    ps = ps or PortfolioSettings.get_solo()
    equity = float(ps.equity)
    computed = cfg.life_status_for_equity(equity)

    # Once DEAD, stay DEAD until manual reset (even if equity somehow rises)
    if ps.life_status == PortfolioSettings.LifeStatus.DEAD:
        if not ps.kill_switch:
            ps.kill_switch = True
            ps.save(update_fields=["kill_switch", "updated_at"])
        return ps

    changed_fields = []
    if computed != ps.life_status:
        prev = ps.life_status
        ps.life_status = computed
        changed_fields.append("life_status")
        if computed == PortfolioSettings.LifeStatus.DEAD:
            ps.kill_switch = True
            changed_fields.append("kill_switch")
            if alert:
                try:
                    from notifications.telegram import send_message

                    send_message(
                        f"StockScope DEAD: equity ₹{equity:.2f} <= floor ₹{cfg.DEAD_FLOOR}. "
                        "All signals OFF until staff reset."
                    )
                except Exception:  # noqa: BLE001
                    pass
        elif equity <= cfg.KILL_EQUITY and not ps.kill_switch:
            ps.kill_switch = True
            changed_fields.append("kill_switch")
        # Log transition for survival entry
        _ = prev
    elif equity <= cfg.KILL_EQUITY and not ps.kill_switch:
        ps.kill_switch = True
        changed_fields.append("kill_switch")

    if changed_fields:
        changed_fields.append("updated_at")
        ps.save(update_fields=list(dict.fromkeys(changed_fields)))
    return ps


def _revenge_symbols_today() -> set[str]:
    """Symbols that hit SL today — block same-day re-entry (revenge)."""
    today = date.today()
    out: set[str] = set()
    qs = Position.objects.filter(
        status=Position.Status.CLOSED,
        closed_at__date=today,
        pnl__lt=0,
    ).select_related("instrument")
    for pos in qs:
        notes = (pos.notes or "").lower()
        # Treat stopped / SL closes as revenge candidates
        if "sl" in notes or "stop" in notes or float(pos.pnl) < 0:
            out.add(pos.instrument.symbol.replace(".NS", "").replace(".BO", "").upper())
    # Also signals stopped today
    for sig in Signal.objects.filter(status=Signal.Status.STOPPED, created_at__date=today).select_related(
        "instrument"
    ):
        out.add(sig.instrument.symbol.replace(".NS", "").replace(".BO", "").upper())
    return out


def get_portfolio_state() -> PortfolioState:
    ps = PortfolioSettings.get_solo()
    _rollover_pnl_windows(ps)
    refresh_life_status(ps)
    rp = cfg.risk_params_for_equity(float(ps.equity))
    # If already DEAD, keep dead params regardless of equity bump without reset
    if ps.life_status == PortfolioSettings.LifeStatus.DEAD:
        rp = cfg.risk_params_for_equity(cfg.DEAD_FLOOR)

    open_count = Position.objects.filter(status=Position.Status.OPEN).count()
    trades_today = Signal.objects.filter(
        created_at__date=date.today(),
        status__in=[
            Signal.Status.NEW,
            Signal.Status.TRIGGERED,
            Signal.Status.CLOSED,
            Signal.Status.TARGET,
            Signal.Status.STOPPED,
        ],
    ).count()
    avg_volumes = {
        i.symbol: (i.avg_volume_20d or 1e9) for i in Instrument.objects.filter(is_active=True)
    }
    min_prob = float(rp.min_prob)
    # Raise gate to champion's optimized decision threshold when available
    try:
        from ml import registry

        champ = registry.champion("swing_lgbm")
        if champ is not None:
            thr = None
            if isinstance(champ.metrics, dict):
                thr = champ.metrics.get("decision_threshold")
            if thr is None:
                try:
                    thr = registry.load(champ).get("decision_threshold")
                except Exception:  # noqa: BLE001
                    thr = None
            if thr is not None:
                min_prob = max(min_prob, float(thr))
    except Exception:  # noqa: BLE001
        pass

    return PortfolioState(
        kill_switch=ps.kill_switch,
        day_pnl=float(ps.day_pnl),
        week_pnl=float(ps.week_pnl),
        trades_today=trades_today,
        open_positions=open_count,
        free_cash=float(ps.cash),
        avg_volumes=avg_volumes,
        equity=float(ps.equity),
        life_status=ps.life_status,
        risk_per_trade=rp.risk_per_trade,
        max_position_pct=rp.max_position_pct,
        max_open_positions=rp.max_open_positions,
        max_trades_per_day=rp.max_trades_per_day,
        min_prob=min_prob,
        lockout_until=ps.lockout_until,
        revenge_symbols=_revenge_symbols_today(),
        consecutive_losses=int(ps.consecutive_losses or 0),
    )


def _rollover_pnl_windows(ps: PortfolioSettings) -> None:
    today = date.today()
    week_key = today.strftime("%G-W%V")
    changed = False
    if ps.day_key != today:
        ps.day_pnl = Decimal("0")
        ps.day_key = today
        changed = True
    if ps.week_key != week_key:
        ps.week_pnl = Decimal("0")
        ps.week_key = week_key
        changed = True
    if changed:
        ps.save()


def record_equity_snapshot(mode: str = "paper") -> EquitySnapshot:
    ps = PortfolioSettings.get_solo()
    return EquitySnapshot.objects.create(
        equity=ps.equity,
        cash=ps.cash,
        open_pnl=Decimal("0"),
        mode=mode,
    )


def paper_gates_passed() -> bool:
    """Go-live when both stocks (and options if used) clear shadow unlock bars.

    Falls back to legacy closed-position gate when shadow book is empty.
    """
    try:
        from portfolio.strategy_gates import strategy_unlocked

        stocks_ok = strategy_unlocked("stocks")
        # Options need unlock only if user has been practicing them
        from evidence.models import ShadowTrade

        has_opts = ShadowTrade.objects.filter(kind="options").exists()
        opts_ok = strategy_unlocked("fno") if has_opts else True
        if stocks_ok and opts_ok:
            # Prefer shadow evidence when enough samples exist
            from evidence.stats import stocks_unlock_status

            if stocks_unlock_status().get("n", 0) >= 10:
                return True
    except Exception:  # noqa: BLE001
        pass
    closed = Position.objects.filter(status=Position.Status.CLOSED, mode="paper")
    pnls = [float(p.pnl) for p in closed]
    if len(pnls) < cfg.PAPER_MIN_SIGNALS:
        return False
    return sum(pnls) > cfg.PAPER_MIN_EXPECTANCY


def _signal_unit_multiplier(signal: Signal) -> int:
    """F&O qty is lots → multiply by lot_size; equity qty is shares → 1."""
    reasons = signal.reasons if isinstance(getattr(signal, "reasons", None), dict) else {}
    fno = reasons.get("fno") if isinstance(reasons.get("fno"), dict) else None
    if fno and fno.get("lot_size"):
        return max(1, int(fno["lot_size"]))
    if getattr(signal, "strategy", "") == "fno_intraday" or reasons.get("kind") == "fno":
        from signals.fno import lot_size_for

        return max(1, int(lot_size_for(signal.instrument.symbol) or 1))
    return 1


def _contract_label(pos: Position | None = None, signal: Signal | None = None, **extra) -> str:
    from portfolio.live import fno_meta

    if pos is not None:
        meta = fno_meta(pos) or {}
        if meta.get("strike"):
            return (
                f"{meta.get('underlying', pos.instrument.symbol)} "
                f"{meta.get('strike')} {meta.get('option_type', '')}"
            ).strip()
        return pos.instrument.symbol
    if signal is not None:
        reasons = signal.reasons if isinstance(signal.reasons, dict) else {}
        fno = reasons.get("fno") if isinstance(reasons.get("fno"), dict) else {}
        if fno.get("strike"):
            return f"{fno.get('underlying', signal.instrument.symbol)} {fno.get('strike')} {fno.get('option_type', '')}".strip()
        return signal.instrument.symbol
    return str(extra.get("contract") or extra.get("symbol") or "")


def _record_fill(
    *,
    position: Position,
    side: str,
    price: Decimal | float,
    qty: int,
    units: int,
    product: str,
    source: str,
    notes: str = "",
    mistake_note: str = "",
    contract: str = "",
) -> OrderFill:
    from portfolio.charges import calc_leg_charges
    from portfolio.live import is_fno

    is_opt = is_fno(position) or str(product).upper() in {"NRML", "OPTIONS", "OPT"}
    ch = calc_leg_charges(
        side=side,  # type: ignore[arg-type]
        price=float(price),
        units=int(units),
        product=product,
        is_option=is_opt,
    )
    turnover = Decimal(str(ch.turnover))
    charges_total = Decimal(str(ch.total))
    if side == "BUY":
        net = -(turnover + charges_total)
    else:
        net = turnover - charges_total
    return OrderFill.objects.create(
        position=position,
        instrument=position.instrument,
        side=side,
        product=product,
        contract=contract or _contract_label(pos=position),
        qty=int(qty),
        units=int(units),
        price=Decimal(str(price)),
        turnover=turnover,
        charges=ch.as_dict(),
        charges_total=charges_total,
        net_cash_flow=Decimal(str(round(float(net), 2))),
        source=source,
        broker="groww",
        notes=notes,
        mistake_note=mistake_note,
    )


def mark_signal_taken(
    signal: Signal,
    mode: str = "paper",
    fill_price: float | None = None,
    qty: int | None = None,
    order_confirmed: bool = True,
    *,
    is_dummy: bool = False,
) -> Position:
    """Confirm Groww order. Debits wallet unless is_dummy (practice / no wallet impact)."""
    if not order_confirmed:
        raise ValueError("order_confirmed must be true to open a position")
    if mode == "live":
        if cfg.FORCE_PAPER_UNTIL_GATES and not paper_gates_passed():
            raise ValueError("Live mode locked until paper go-live gates PASS")
    ps = PortfolioSettings.get_solo()
    refresh_life_status(ps)
    if ps.life_status == PortfolioSettings.LifeStatus.DEAD and not is_dummy:
        raise ValueError("Portfolio is DEAD — no new positions until staff reset")

    entry = Decimal(str(fill_price if fill_price is not None else signal.entry_trigger))
    q = int(qty if qty is not None else signal.qty)
    if q < 1:
        raise ValueError("qty must be >= 1")
    mult = _signal_unit_multiplier(signal)
    units = q * mult
    product = getattr(signal, "product", None) or "CNC"

    note = "practice track (wallet unchanged)" if is_dummy else "user confirmed Groww order"
    if mult > 1 and not is_dummy:
        note = f"user confirmed Groww | {q} lot×{mult} | lot_reserved"

    pos = Position.objects.create(
        instrument=signal.instrument,
        signal=signal,
        side=signal.side,
        qty=q,
        entry_price=entry,
        stop=signal.sl_trigger,
        target=signal.target1,
        planned_entry=signal.entry_trigger,
        planned_stop=signal.sl_trigger,
        planned_target=signal.target1,
        mode=mode,
        notes=note,
        is_dummy=bool(is_dummy),
    )
    fill = _record_fill(
        position=pos,
        side="BUY" if signal.side == "BUY" else "SELL",
        price=entry,
        qty=q,
        units=units,
        product=product,
        source=OrderFill.Source.SIGNAL,
        notes=note,
        contract=_contract_label(signal=signal),
    )
    reserved = abs(Decimal(fill.net_cash_flow))
    pos.reserved_cash = reserved if not is_dummy else Decimal("0")
    pos.save(update_fields=["reserved_cash"])
    signal.status = Signal.Status.TRIGGERED
    signal.mode = mode
    from django.utils import timezone

    signal.taken_at = timezone.now()
    signal.save(update_fields=["status", "mode", "taken_at"])
    if not is_dummy:
        ps.cash = Decimal(ps.cash) + Decimal(fill.net_cash_flow)  # negative for BUY
        ps.save(update_fields=["cash", "updated_at"])
    return pos


def confirm_skip(signal: Signal, reason: str = "I did not place this order") -> Signal:
    signal.status = Signal.Status.SKIPPED
    signal.skip_reason = reason
    signal.save(update_fields=["status", "skip_reason"])
    return signal


def repair_open_fno_reservations() -> int:
    """One-time fix: older F&O opens reserved entry×lots instead of entry×lots×lot_size."""
    from portfolio.live import capital_reserved, is_fno

    ps = PortfolioSettings.get_solo()
    fixed = 0
    for pos in Position.objects.filter(status=Position.Status.OPEN).select_related("instrument", "signal"):
        if not is_fno(pos):
            continue
        if "lot_reserved" in (pos.notes or ""):
            continue
        # Previously deducted entry * qty (lots only)
        old = Decimal(str(pos.entry_price)) * Decimal(int(pos.qty))
        correct = capital_reserved(pos)
        delta = correct - old
        if delta > 0:
            ps.cash = Decimal(ps.cash) - delta
            pos.notes = ((pos.notes or "").strip() + " | lot_reserved").strip(" |")
            pos.save(update_fields=["notes"])
            fixed += 1
    if fixed:
        ps.save(update_fields=["cash", "updated_at"])
    return fixed


def close_position(
    pos: Position,
    exit_price: float,
    reason: str = "",
    *,
    exit_reason: str | None = None,
    mistake_tags: list[str] | None = None,
    review_note: str = "",
    require_review: bool = True,
) -> Position:
    from datetime import timedelta
    from django.utils import timezone
    from portfolio.live import is_fno, qty_units

    if require_review:
        if not exit_reason or not mistake_tags:
            raise ValueError(
                "Trade review required: exit_reason and mistake_tags "
                "(or followed_signal_correctly)"
            )

    exit_p = Decimal(str(exit_price))
    units_i = qty_units(pos)
    product = "NRML" if is_fno(pos) else (getattr(pos.signal, "product", None) if pos.signal_id else "CNC")
    product = product or "CNC"
    # Sell leg (square-off) for a long; buy-to-cover for a short
    exit_side = "SELL" if pos.side == "BUY" else "BUY"
    fill = _record_fill(
        position=pos,
        side=exit_side,
        price=exit_p,
        qty=int(pos.qty),
        units=units_i,
        product=product,
        source=OrderFill.Source.CLOSE,
        notes=reason or exit_reason or "square off",
    )
    # Net P&L from all fills on this position (includes Groww charges)
    fills = list(pos.fills.all())
    net_pnl = sum((Decimal(f.net_cash_flow) for f in fills), Decimal("0"))
    pos.exit_price = exit_p
    pos.pnl = net_pnl
    pos.status = Position.Status.CLOSED
    pos.closed_at = timezone.now()
    pos.notes = reason or exit_reason or ""
    pos.save()
    ps = PortfolioSettings.get_solo()
    # Dummy / practice orders do not move the real paper wallet
    if not pos.is_dummy:
        ps.cash = Decimal(ps.cash) + Decimal(fill.net_cash_flow)
        ps.equity = Decimal(ps.equity) + net_pnl
        ps.day_pnl = Decimal(ps.day_pnl) + net_pnl
        ps.week_pnl = Decimal(ps.week_pnl) + net_pnl

        # Consecutive losses → cooling-off lockout
        if float(net_pnl) < 0:
            ps.consecutive_losses = int(ps.consecutive_losses or 0) + 1
            if ps.consecutive_losses >= cfg.COOLING_OFF_LOSSES:
                ps.lockout_until = timezone.now() + timedelta(hours=cfg.COOLING_OFF_HOURS)
        else:
            ps.consecutive_losses = 0

        ps.save()
        refresh_life_status(ps)

    mapped = exit_reason or reason
    if pos.signal_id:
        if mapped in {"stop", "sl"}:
            pos.signal.status = Signal.Status.STOPPED
        elif mapped == "target":
            pos.signal.status = Signal.Status.TARGET
        else:
            pos.signal.status = Signal.Status.CLOSED
        pos.signal.save(update_fields=["status"])

    if require_review and exit_reason and mistake_tags:
        from marketdata.regime import detect_regime
        from portfolio.reviews import create_trade_review

        create_trade_review(
            pos,
            exit_reason=exit_reason if exit_reason != "stop" else "sl",
            mistake_tags=list(mistake_tags),
            note=review_note or "",
            regime_snapshot=detect_regime(),
        )

    record_equity_snapshot(mode=pos.mode)
    return pos


def confirm_levels_updated(pos: Position, *, stop: float, target: float) -> Position:
    """User confirms they updated SL/target in Groww — log adjustment history."""
    from django.utils import timezone
    from portfolio.management import apply_user_levels

    old_sl, old_tgt = float(pos.stop), float(pos.target)
    meta = apply_user_levels(
        old_sl=old_sl, old_tgt=old_tgt, new_sl=stop, new_tgt=target, side=pos.side
    )
    log = list(pos.adjustment_log or [])
    log.append({**meta, "ts": timezone.now().isoformat()})
    pos.adjustment_log = log
    pos.stop = Decimal(str(stop))
    pos.target = Decimal(str(target))
    pos.save(update_fields=["stop", "target", "adjustment_log"])
    return pos


def add_manual_groww_order(
    *,
    symbol: str,
    side: str = "BUY",
    product: str = "NRML",
    qty: int = 1,
    lot_size: int | None = None,
    entry_price: float,
    stop: float | None = None,
    target: float | None = None,
    exit_price: float | None = None,
    option_type: str | None = None,
    strike: float | None = None,
    expiry: str = "",
    notes: str = "",
    mistake_note: str = "",
    is_dummy: bool = False,
) -> Position:
    """Record a Groww order (paper). Default original; set is_dummy for practice-only."""
    from signals.fno import lot_size_for

    sym = symbol.upper().replace(".NS", "").strip()
    if not sym:
        raise ValueError("symbol required")
    q = max(1, int(qty))
    is_opt = bool(option_type) or str(product).upper() in {"NRML", "OPTIONS"}
    ls = int(lot_size or (lot_size_for(sym) if is_opt else 1) or 1)
    units = q * ls if is_opt else q
    entry = Decimal(str(entry_price))
    if entry <= 0:
        raise ValueError("entry_price must be > 0")
    inst, _ = Instrument.objects.get_or_create(symbol=sym)
    sl = Decimal(str(stop if stop is not None else round(float(entry) * 0.65, 2)))
    tg = Decimal(str(target if target is not None else round(float(entry) * 1.6, 2)))
    contract = sym
    if is_opt and strike is not None:
        contract = f"{sym} {float(strike):g} {(option_type or 'CE').upper()}"
        if expiry:
            contract = f"{contract} {expiry}"

    # Stash F&O meta on a lightweight synthetic via position notes + optional signal skip
    pos = Position.objects.create(
        instrument=inst,
        signal=None,
        side=side.upper(),
        qty=q,
        entry_price=entry,
        stop=sl,
        target=tg,
        mode="paper",
        is_dummy=bool(is_dummy),
        notes=(notes or "manual Groww record") + (" | lot_reserved" if is_opt else ""),
    )
    # Attach fno-like meta for live marks via a tiny Signal? Prefer storing JSON on notes is weak.
    # Create a skipped/manual Signal so fno_meta works.
    if is_opt:
        from django.utils import timezone
        from signals.models import Signal as Sig

        fno = {
            "underlying": sym,
            "strike": float(strike) if strike is not None else None,
            "option_type": (option_type or "CE").upper(),
            "lot_size": ls,
            "lots": q,
            "expiry_label": expiry,
            "product": "NRML",
        }
        sig = Sig.objects.create(
            instrument=inst,
            side=side.upper(),
            strategy="fno_intraday",
            product="NRML",
            entry_trigger=entry,
            entry_limit=entry,
            sl_trigger=sl,
            sl_limit=sl,
            target1=tg,
            target2=tg,
            chase_limit=entry,
            qty=q,
            capital_used=float(entry) * units,
            max_loss=0,
            rr_net=0,
            probability=0,
            time_machine={},
            gate={"passed": True, "reasons": ["manual_groww"]},
            reasons={"kind": "fno", "fno": fno, "manual_groww": True},
            status=Sig.Status.TRIGGERED,
            mode="paper",
            data_as_of=timezone.now(),
            data_source="manual_groww",
            data_age_min=0,
            valid_until=timezone.now(),
        )
        pos.signal = sig
        pos.save(update_fields=["signal"])

    buy_side = "BUY" if side.upper() == "BUY" else "SELL"
    fill = _record_fill(
        position=pos,
        side=buy_side,
        price=entry,
        qty=q,
        units=units,
        product=product if not is_opt else "NRML",
        source=OrderFill.Source.MANUAL,
        notes=notes or "manual Groww record",
        mistake_note=mistake_note,
        contract=contract,
    )
    reserved = abs(Decimal(fill.net_cash_flow))
    pos.reserved_cash = reserved
    pos.save(update_fields=["reserved_cash"])
    ps = PortfolioSettings.get_solo()
    if not pos.is_dummy:
        ps.cash = Decimal(ps.cash) + Decimal(fill.net_cash_flow)
        ps.save(update_fields=["cash", "updated_at"])

    if exit_price is not None:
        close_position(pos, float(exit_price), reason=mistake_note or "manual square-off")
        if mistake_note:
            # keep mistake on sell fill too
            last = pos.fills.filter(side="SELL").order_by("-placed_at").first()
            if last and not last.mistake_note:
                last.mistake_note = mistake_note
                last.save(update_fields=["mistake_note"])
    return pos


def _cash_to_restore(pos: Position) -> Decimal:
    """Cash to put back when deleting an open position (undo fill wallet effects)."""
    fills = list(pos.fills.all()) if hasattr(pos, "fills") else []
    if fills:
        # Undo each fill's net_cash_flow (BUY is negative → restoring adds cash)
        return -sum((Decimal(f.net_cash_flow) for f in fills), Decimal("0"))
    if pos.reserved_cash is not None:
        return Decimal(pos.reserved_cash)
    from portfolio.live import capital_reserved, is_fno

    if is_fno(pos) and "lot_reserved" not in (pos.notes or ""):
        return Decimal(str(pos.entry_price)) * Decimal(int(pos.qty))
    return capital_reserved(pos)


def delete_position(pos: Position) -> dict:
    """Remove order from board. Open → restore wallet; closed → keep realized P&L, drop row."""
    if pos.status == Position.Status.OPEN:
        ps = PortfolioSettings.get_solo()
        ps.cash = Decimal(ps.cash) + _cash_to_restore(pos)
        if float(ps.cash) > float(ps.equity) + 0.05 and not Position.objects.filter(
            status=Position.Status.OPEN
        ).exclude(pk=pos.pk).exists():
            ps.cash = Decimal(ps.equity)
        ps.save(update_fields=["cash", "updated_at"])
        if pos.signal_id:
            pos.signal.status = Signal.Status.SKIPPED
            pos.signal.skip_reason = "position deleted by user"
            pos.signal.save(update_fields=["status", "skip_reason"])
    pos_id = pos.id
    pos.delete()  # cascades OrderFill
    return {"deleted": pos_id}


def set_position_dummy(pos: Position, is_dummy: bool) -> Position:
    """Mark order as dummy (practice) or original. Works for open and closed.

    Dummy trades are excluded from day book / Today P&L. Wallet is adjusted so
    dummy does not affect equity/cash totals.
    """
    want = bool(is_dummy)
    if bool(pos.is_dummy) == want:
        return pos

    ps = PortfolioSettings.get_solo()
    if pos.status == Position.Status.OPEN:
        restore = _cash_to_restore(pos)
        if want:
            # Leaving the real book — put reserved cash back
            ps.cash = Decimal(ps.cash) + restore
        else:
            # Re-entering the real book — lock cash again
            ps.cash = Decimal(ps.cash) - restore
        ps.save(update_fields=["cash", "updated_at"])
    else:
        # Closed: reverse or re-apply realized P&L on equity / day / week / cash
        pnl = Decimal(pos.pnl or 0)
        if want:
            ps.equity = Decimal(ps.equity) - pnl
            ps.day_pnl = Decimal(ps.day_pnl) - pnl
            ps.week_pnl = Decimal(ps.week_pnl) - pnl
            ps.cash = Decimal(ps.cash) - pnl
        else:
            ps.equity = Decimal(ps.equity) + pnl
            ps.day_pnl = Decimal(ps.day_pnl) + pnl
            ps.week_pnl = Decimal(ps.week_pnl) + pnl
            ps.cash = Decimal(ps.cash) + pnl
        ps.save(update_fields=["equity", "cash", "day_pnl", "week_pnl", "updated_at"])
        refresh_life_status(ps, alert=False)

    pos.is_dummy = want
    pos.save(update_fields=["is_dummy"])
    return pos


def reconcile_paper_cash() -> PortfolioSettings:
    """Clamp runaway cash (e.g. legacy delete restored full lot when only premium×1 was locked)."""
    ps = PortfolioSettings.get_solo()
    opens = list(Position.objects.filter(status=Position.Status.OPEN))
    locked = sum((_cash_to_restore(p) for p in opens), Decimal("0"))
    # With no opens, cash should equal equity for a clean paper book
    if not opens and abs(float(ps.cash) - float(ps.equity)) > 0.05:
        ps.cash = Decimal(ps.equity)
        ps.save(update_fields=["cash", "updated_at"])
    elif opens and float(ps.cash) > float(ps.equity):
        ps.cash = max(Decimal("0"), Decimal(ps.equity) - locked)
        ps.save(update_fields=["cash", "updated_at"])
    return ps


def edit_position(
    pos: Position,
    *,
    entry_price: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    qty: int | None = None,
) -> Position:
    """Edit open order levels / lots. Adjusts cash for entry or qty changes."""
    from portfolio.live import capital_reserved, qty_units

    if pos.status != Position.Status.OPEN:
        raise ValueError("only open positions can be edited")
    ps = PortfolioSettings.get_solo()
    old_cap = _cash_to_restore(pos)

    if entry_price is not None:
        pos.entry_price = Decimal(str(entry_price))
    if stop is not None:
        pos.stop = Decimal(str(stop))
    if target is not None:
        pos.target = Decimal(str(target))
    if qty is not None:
        q = int(qty)
        if q < 1:
            raise ValueError("qty must be >= 1")
        pos.qty = q

    new_cap = capital_reserved(pos)
    pos.reserved_cash = new_cap
    if "lot_reserved" not in (pos.notes or "") and new_cap > old_cap:
        pos.notes = ((pos.notes or "") + " | lot_reserved").strip(" |")
    pos.save()
    delta = new_cap - old_cap
    if delta != 0:
        ps.cash = Decimal(ps.cash) - delta
        ps.save(update_fields=["cash", "updated_at"])
    # Keep linked signal in sync for display
    if pos.signal_id:
        sig = pos.signal
        fields = []
        if entry_price is not None:
            sig.entry_trigger = pos.entry_price
            sig.entry_limit = pos.entry_price
            fields += ["entry_trigger", "entry_limit"]
        if stop is not None:
            sig.sl_trigger = pos.stop
            sig.sl_limit = pos.stop
            fields += ["sl_trigger", "sl_limit"]
        if target is not None:
            sig.target1 = pos.target
            fields.append("target1")
        if qty is not None:
            sig.qty = pos.qty
            fields.append("qty")
        if fields:
            sig.capital_used = float(new_cap)
            fields.append("capital_used")
            sig.save(update_fields=list(dict.fromkeys(fields)))
    _ = qty_units  # imported for clarity / future hooks
    return pos


def reanalyze_position(pos: Position) -> dict:
    """Fresh mark + management action (SL tighten-only) + optional F&O plan."""
    from portfolio.live import enrich_position, fno_meta, is_fno
    from portfolio.management import suggest_management

    live = enrich_position(pos)
    # enrich_position stores numeric LTP under "ltp"; "mark" is the raw quote dict — never float() it
    mark = live.get("ltp")
    raw_mark = live.get("mark")
    if mark is None and isinstance(raw_mark, dict):
        mark = raw_mark.get("ltp")
    atr = None
    plan = None
    if is_fno(pos):
        meta = fno_meta(pos) or {}
        underlying = str(meta.get("underlying") or pos.instrument.symbol).upper()
        opt = str(meta.get("option_type") or "CE")
        try:
            from features.indicators import add_indicators
            from marketdata.service import MarketDataService
            from signals.fno import build_fno_plan

            svc = MarketDataService()
            yf_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
            yf_sym = yf_map.get(underlying) or f"{underlying}.NS"
            df = svc.bars_df(yf_sym, interval="1d")
            if df is not None and len(df) >= 60:
                ind = add_indicators(df)
                spot = float(ind["close"].iloc[-1])
                atr = float(ind["atr14"].iloc[-1])
                plan = build_fno_plan(
                    underlying,
                    spot,
                    bullish=(opt.upper() in {"CE", "CALL"}),
                    atr=atr,
                    capital=float(PortfolioSettings.get_solo().cash),
                    option_type=opt,
                )
        except Exception as exc:  # noqa: BLE001
            plan = {"error": str(exc)[:200]}

    mgmt = None
    action_changed = False
    try:
        mark_f = float(mark) if mark is not None else 0.0
    except (TypeError, ValueError):
        mark_f = 0.0
    if mark_f > 0:
        mgmt = suggest_management(
            entry=float(pos.entry_price),
            stop=float(pos.stop),
            target=float(pos.target),
            mark=mark_f,
            side=pos.side,
            atr=atr,
            is_option=is_fno(pos),
        )
        prev = pos.last_mgmt_action or ""
        # Same call, refreshed levels: tighten SL / raise target when management says so (never widen SL)
        levels_updated = False
        sug_sl = mgmt.get("suggested_sl")
        sug_tg = mgmt.get("suggested_target")
        try:
            if sug_sl is not None and float(sug_sl) > 0 and abs(float(sug_sl) - float(pos.stop)) > 1e-6:
                # Only apply if it is a tighten vs current stop
                if pos.side == "BUY" and float(sug_sl) >= float(pos.stop) - 1e-9:
                    pos.stop = Decimal(str(round(float(sug_sl), 2)))
                    levels_updated = True
                elif pos.side == "SELL" and float(sug_sl) <= float(pos.stop) + 1e-9:
                    pos.stop = Decimal(str(round(float(sug_sl), 2)))
                    levels_updated = True
            if sug_tg is not None and float(sug_tg) > 0 and abs(float(sug_tg) - float(pos.target)) > 1e-6:
                if pos.side == "BUY" and float(sug_tg) >= float(pos.target) - 1e-9:
                    pos.target = Decimal(str(round(float(sug_tg), 2)))
                    levels_updated = True
                elif pos.side == "SELL" and float(sug_tg) <= float(pos.target) + 1e-9:
                    pos.target = Decimal(str(round(float(sug_tg), 2)))
                    levels_updated = True
        except (TypeError, ValueError):
            pass
        if mgmt.get("action") and mgmt["action"] != prev:
            action_changed = True
            pos.last_mgmt_action = mgmt["action"]
        if levels_updated or action_changed:
            fields = ["last_mgmt_action"] if action_changed else []
            if levels_updated:
                fields += ["stop", "target"]
            pos.save(update_fields=list(dict.fromkeys(fields)) or None)
            # Keep linked signal levels in sync for the same call
            if levels_updated and pos.signal_id:
                sig = pos.signal
                sig.sl_trigger = pos.stop
                sig.sl_limit = pos.stop
                sig.target1 = pos.target
                sig.save(update_fields=["sl_trigger", "sl_limit", "target1"])
            if action_changed:
                try:
                    from notifications.telegram import send_message

                    send_message(
                        f"StockScope action → {mgmt['action']} on {pos.instrument.symbol}: {mgmt.get('reason')}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            # Refresh live snapshot after level change
            live = enrich_position(pos)
        live["management"] = mgmt
        live["action_changed"] = action_changed
        live["levels_updated"] = levels_updated
    return {"live": live, "plan": plan, "management": mgmt, "action_changed": action_changed}


def arm_kill_switch(on: bool = True) -> PortfolioSettings:
    ps = PortfolioSettings.get_solo()
    if ps.life_status == PortfolioSettings.LifeStatus.DEAD and not on:
        # Cannot disarm kill while DEAD — use reset_life_status
        return ps
    ps.kill_switch = on
    ps.save(update_fields=["kill_switch", "updated_at"])
    return ps


def reset_life_status(equity: float | None = None, cash: float | None = None) -> PortfolioSettings:
    """Staff-only: clear DEAD and restore life. Defaults equity/cash to CAPITAL."""
    ps = PortfolioSettings.get_solo()
    eq = Decimal(str(equity if equity is not None else cfg.CAPITAL))
    ca = Decimal(str(cash if cash is not None else eq))
    if float(eq) <= cfg.DEAD_FLOOR:
        raise ValueError(f"Reset equity must be above DEAD_FLOOR ({cfg.DEAD_FLOOR})")
    ps.equity = eq
    ps.cash = ca
    ps.start_equity = eq
    ps.kill_switch = False
    ps.life_status = cfg.life_status_for_equity(float(eq))
    ps.day_pnl = Decimal("0")
    ps.week_pnl = Decimal("0")
    ps.consecutive_losses = 0
    ps.lockout_until = None
    ps.save()
    try:
        from notifications.telegram import send_message

        send_message(f"StockScope life reset: status={ps.life_status} equity=₹{float(eq):.2f}")
    except Exception:  # noqa: BLE001
        pass
    return ps
