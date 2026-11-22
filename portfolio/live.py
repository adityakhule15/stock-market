"""Live marks, lot sizing, and action suggestions for open positions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from portfolio.models import Position
from signals.fno import LOT_SIZE, lot_size_for


def signal_reasons(pos: Position) -> dict:
    sig = pos.signal
    if not sig:
        return {}
    r = getattr(sig, "reasons", None) or {}
    return r if isinstance(r, dict) else {}


def fno_meta(pos: Position) -> dict | None:
    r = signal_reasons(pos)
    fno = r.get("fno") if isinstance(r.get("fno"), dict) else None
    if fno:
        return fno
    sig = pos.signal
    if sig and getattr(sig, "strategy", "") == "fno_intraday":
        sym = pos.instrument.symbol.upper()
        return {
            "lot_size": lot_size_for(sym) or LOT_SIZE.get(sym, 1),
            "lots": int(pos.qty or 1),
            "underlying": sym,
            "option_type": "CE",
            "strike": None,
        }
    if sig and getattr(sig, "product", "") == "NRML" and r.get("kind") == "fno":
        sym = pos.instrument.symbol.upper()
        return {
            "lot_size": lot_size_for(sym) or LOT_SIZE.get(sym, 1),
            "lots": int(pos.qty or 1),
            "underlying": sym,
        }
    return None


def is_fno(pos: Position) -> bool:
    return fno_meta(pos) is not None


def lot_size(pos: Position) -> int:
    meta = fno_meta(pos)
    if not meta:
        return 1
    return int(meta.get("lot_size") or lot_size_for(pos.instrument.symbol) or 1)


def lots(pos: Position) -> int:
    """For F&O, Position.qty is number of lots. For equity, qty is shares."""
    return int(pos.qty or 0)


def unit_multiplier(pos: Position) -> int:
    """Shares (or option units) per Position.qty unit."""
    return lot_size(pos) if is_fno(pos) else 1


def qty_units(pos: Position) -> int:
    """Total tradable units (shares / option contracts units)."""
    return lots(pos) * unit_multiplier(pos)


def qty_label(pos: Position) -> str:
    if is_fno(pos):
        ls = lot_size(pos)
        n = lots(pos)
        return f"{n} lot × {ls} (= {n * ls})"
    return str(int(pos.qty))


def capital_reserved(pos: Position) -> Decimal:
    if getattr(pos, "reserved_cash", None) is not None:
        return Decimal(str(pos.reserved_cash))
    return Decimal(str(pos.entry_price)) * Decimal(qty_units(pos))


def unrealized_pnl(pos: Position, mark: float) -> float:
    direction = 1 if pos.side == "BUY" else -1
    return round((float(mark) - float(pos.entry_price)) * qty_units(pos) * direction, 2)


def suggest_action(pos: Position, mark: float | None, *, mark_meta: dict | None = None) -> dict[str, str]:
    """Map management engine → UI action chip (SL never widened by suggestion)."""
    if mark is None or mark <= 0:
        return {
            "code": "WAIT",
            "label": "WAIT — no live price yet",
            "detail": "Refresh in a moment or paste LTP after Edit.",
        }
    stale = bool((mark_meta or {}).get("stale") or (mark_meta or {}).get("approx_strike"))
    from portfolio.management import suggest_management

    entry = float(pos.entry_price)
    stop = float(pos.stop)
    target = float(pos.target)
    side = pos.side.upper()
    pnl = unrealized_pnl(pos, mark)
    pnl_txt = f"₹{pnl:+.2f}"
    mgmt = suggest_management(
        entry=entry, stop=stop, target=target, mark=float(mark), side=side, is_option=is_fno(pos)
    )
    code = mgmt.get("action") or "HOLD"
    label_map = {
        "HOLD": "HOLD",
        "MOVE_SL_BREAKEVEN": "MOVE SL TO BREAKEVEN",
        "TRAIL_SL": f"TRAIL SL TO {mgmt.get('suggested_sl')}",
        "BOOK_PARTIAL": "BOOK PARTIAL",
        "RAISE_TARGET": f"RAISE TARGET TO {mgmt.get('suggested_target')}",
        "EXIT_NOW": "EXIT NOW",
    }
    detail = f"{mgmt.get('reason')} · open P&L {pnl_txt}"
    if stale:
        src = (mark_meta or {}).get("source") or "approx"
        detail = f"mark ≈ ₹{float(mark):.2f} ({src}) · {detail}"
    return {
        "code": code,
        "label": label_map.get(code, code),
        "detail": detail,
        "suggested_sl": mgmt.get("suggested_sl"),
        "suggested_target": mgmt.get("suggested_target"),
        "r_multiple": mgmt.get("r_multiple"),
        "stale_mark": stale,
    }


def _ltp_from_chain(
    chain: dict, strike: float, opt: str, *, max_dist: float = 0.51
) -> tuple[float | None, dict]:
    """Exact strike first; if ``max_dist`` allows, nearest strike with LTP > 0."""
    best = None
    best_dist = 1e18
    best_ltp_row = None
    best_ltp_dist = 1e18
    key = "ce_ltp" if str(opt).upper() in {"CE", "CALL"} else "pe_ltp"
    for row in chain.get("rows") or []:
        try:
            s = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        dist = abs(s - float(strike))
        if dist < best_dist:
            best_dist = dist
            best = row
        try:
            row_ltp = float(row.get(key) or 0)
        except (TypeError, ValueError):
            row_ltp = 0.0
        if row_ltp > 0 and dist < best_ltp_dist:
            best_ltp_dist = dist
            best_ltp_row = row
    use = best
    use_dist = best_dist
    # Prefer nearest row that actually has a usable LTP when exact is blank/missing
    if (not use or use_dist > max_dist or float((use or {}).get(key) or 0) <= 0) and best_ltp_row:
        if best_ltp_dist <= max_dist:
            use = best_ltp_row
            use_dist = best_ltp_dist
    if not use or use_dist > max_dist + 1e-9:
        return None, {
            "reason": f"strike {strike} not in chain (nearest dist {round(use_dist, 2) if use else '—'})"
        }
    ltp = float(use.get(key) or 0)
    if ltp <= 0:
        return None, {"reason": "LTP 0 on chain", "strike_matched": use.get("strike")}
    approx = abs(float(use.get("strike") or strike) - float(strike)) > 0.51
    return ltp, {
        "strike_matched": use.get("strike"),
        "approx_strike": approx,
        "strike_dist": round(use_dist, 2),
        "source": chain.get("source"),
        "as_of_label": chain.get("as_of_label"),
        "spot": chain.get("spot"),
        "expiry": chain.get("expiry"),
    }


def _fno_fallback_ltp(pos: Position, meta: dict) -> tuple[float | None, dict]:
    """Last-known premium so open book never blanks LTP/P&L columns."""
    live = meta.get("live") if isinstance(meta.get("live"), dict) else {}
    for key, src in (
        ("live_ltp", "signal_live_ltp"),
        ("last_ltp", "signal_last_ltp"),
    ):
        try:
            v = float(live.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v, {"source": src, "stale": True, "as_of_label": live.get("chain_as_of")}
    for key, src in (("buy_ltp", "plan_buy_ltp"), ("est_premium", "plan_est_premium")):
        try:
            v = float(meta.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v, {"source": src, "stale": True}
    try:
        entry = float(pos.entry_price)
    except (TypeError, ValueError):
        entry = 0.0
    if entry > 0:
        return entry, {"source": "entry_fallback", "stale": True}
    return None, {"reason": "no fallback premium"}


def fetch_mark_price(pos: Position, chains: dict[str, dict] | None = None) -> dict[str, Any]:
    """Return live mark for F&O (option LTP) or equity (last price).

    Pass ``chains`` (underlying → chain dict) to reuse one fetch across many positions.
    Always tries a display price for open F&O (wide chain → nearest → last known → entry).
    """
    meta = fno_meta(pos)
    if meta:
        underlying = str(meta.get("underlying") or pos.instrument.symbol).upper()
        strike = meta.get("strike")
        opt = str(meta.get("option_type") or "CE").upper()
        expiry = meta.get("expiry_label") or meta.get("expiry")
        try:
            if strike is None:
                fb, info = _fno_fallback_ltp(pos, meta)
                if fb:
                    return {
                        "ok": True,
                        "ltp": round(float(fb), 2),
                        "kind": "fno",
                        "underlying": underlying,
                        "opt": opt,
                        "error": "missing strike — using fallback premium",
                        **info,
                    }
                return {
                    "ok": False,
                    "ltp": None,
                    "error": "missing strike on signal",
                    "underlying": underlying,
                }

            chain = None
            if chains is not None and underlying in chains:
                chain = chains[underlying]
            if chain is None:
                from marketdata.option_chain import get_chain_cached_or_live

                chain = get_chain_cached_or_live(
                    underlying, expiry=expiry, max_age_sec=45, strike_count=50
                )

            ltp, info = _ltp_from_chain(chain, float(strike), opt, max_dist=0.51)
            # Widen INDstocks window for deep OTM opens (e.g. 26500 vs ATM ~25300)
            if ltp is None:
                try:
                    from marketdata.option_chain import get_chain_cached_or_live

                    wide = get_chain_cached_or_live(
                        underlying, expiry=expiry, max_age_sec=30, strike_count=60
                    )
                    if chains is not None:
                        chains[underlying] = wide
                    ltp, info = _ltp_from_chain(wide, float(strike), opt, max_dist=0.51)
                    chain = wide
                except Exception:  # noqa: BLE001
                    pass
            # Nearest listed strike within 150 pts (still a usable mark for the book)
            if ltp is None and chain:
                ltp, info = _ltp_from_chain(chain, float(strike), opt, max_dist=150.0)
                if ltp is not None:
                    info = {**info, "approx_strike": True}
            if ltp is None:
                fb, finfo = _fno_fallback_ltp(pos, meta)
                if fb:
                    return {
                        "ok": True,
                        "ltp": round(float(fb), 2),
                        "kind": "fno",
                        "underlying": underlying,
                        "opt": opt,
                        "strike": float(strike),
                        "error": (info or {}).get("reason"),
                        **finfo,
                    }
                return {
                    "ok": False,
                    "ltp": None,
                    "error": (info or {}).get("reason") or "no LTP",
                    **(info or {}),
                    "underlying": underlying,
                    "opt": opt,
                }
            return {
                "ok": True,
                "ltp": round(float(ltp), 2),
                "kind": "fno",
                "underlying": underlying,
                "opt": opt,
                "strike": float(strike),
                **info,
            }
        except Exception as exc:  # noqa: BLE001
            fb, finfo = _fno_fallback_ltp(pos, meta)
            if fb:
                return {
                    "ok": True,
                    "ltp": round(float(fb), 2),
                    "kind": "fno",
                    "error": str(exc)[:120],
                    **finfo,
                }
            return {"ok": False, "ltp": None, "error": str(exc)[:180], "kind": "fno"}

    # Equity / normal — prefer shared quote cache when provided via chains["__eq__:{SYM}"]
    sym = pos.instrument.symbol
    eq_key = f"__eq__:{sym.upper()}"
    if chains is not None and eq_key in chains:
        q = chains[eq_key] or {}
        ltp = q.get("ltp")
        if ltp:
            return {"ok": True, "ltp": round(float(ltp), 2), "kind": "equity", "source": q.get("source", "cache")}
    try:
        from marketdata.service import MarketDataService

        svc = MarketDataService()
        yf = sym if "." in sym or sym.startswith("^") else f"{sym}.NS"
        df = svc.bars_df(yf, interval="1d")
        if df is not None and len(df):
            ltp = float(df["close"].iloc[-1])
            return {"ok": True, "ltp": round(ltp, 2), "kind": "equity", "source": "bars_1d"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "ltp": None, "error": str(exc)[:160], "kind": "equity"}
    return {"ok": False, "ltp": None, "error": "no equity quote", "kind": "equity"}


def enrich_position(pos: Position, chains: dict[str, dict] | None = None) -> dict[str, Any]:
    mark = fetch_mark_price(pos, chains=chains)
    # Prefer ok LTP; if mark failed but still returned a number, use it for the book
    ltp = mark.get("ltp")
    if ltp is not None:
        try:
            ltp = float(ltp)
            if ltp <= 0:
                ltp = None
        except (TypeError, ValueError):
            ltp = None
    suggestion = suggest_action(pos, float(ltp) if ltp else None, mark_meta=mark)
    open_pnl = unrealized_pnl(pos, float(ltp)) if ltp else None
    meta = fno_meta(pos) or {}
    return {
        "id": pos.id,
        "symbol": pos.instrument.symbol,
        "side": pos.side,
        "status": pos.status,
        "entry": float(pos.entry_price),
        "stop": float(pos.stop),
        "target": float(pos.target),
        "qty": int(pos.qty),
        "qty_label": qty_label(pos),
        "lot_size": lot_size(pos) if is_fno(pos) else None,
        "lots": lots(pos) if is_fno(pos) else None,
        "units": qty_units(pos),
        "is_fno": is_fno(pos),
        "option_type": meta.get("option_type"),
        "strike": meta.get("strike"),
        "expiry_label": meta.get("expiry_label") or meta.get("expiry"),
        "contract": (
            f"{meta.get('underlying', pos.instrument.symbol)} "
            f"{meta.get('strike')} {meta.get('option_type')}"
            if is_fno(pos) and meta.get("strike")
            else pos.instrument.symbol
        ),
        "ltp": ltp,
        "open_pnl": open_pnl,
        "capital": float(capital_reserved(pos)),
        "invested": float(capital_reserved(pos)),
        "current_value": (
            round(float(ltp) * qty_units(pos), 2) if ltp is not None else None
        ),
        "pnl_pct": (
            round(100.0 * float(open_pnl) / float(capital_reserved(pos)), 2)
            if open_pnl is not None and float(capital_reserved(pos)) > 0
            else None
        ),
        "product": getattr(pos.signal, "product", None) if pos.signal_id else None,
        "suggestion": suggestion,
        "mark": mark,
        "closed_pnl": float(pos.pnl) if pos.status == Position.Status.CLOSED else None,
        "fills": _fills_payload(pos),
        "charges_paid": _charges_paid(pos),
        "open_pnl_net": _open_pnl_net(pos, ltp, open_pnl),
        "is_dummy": bool(getattr(pos, "is_dummy", False)),
    }


def _fills_payload(pos: Position) -> list[dict]:
    out = []
    try:
        fills = pos.fills.all().order_by("placed_at")
    except Exception:  # noqa: BLE001
        return out
    for f in fills:
        out.append(
            {
                "id": f.id,
                "side": f.side,
                "price": float(f.price),
                "qty": f.qty,
                "units": f.units,
                "turnover": float(f.turnover),
                "charges_total": float(f.charges_total),
                "charges": f.charges or {},
                "net_cash_flow": float(f.net_cash_flow),
                "contract": f.contract,
                "product": f.product,
                "source": f.source,
                "broker": f.broker,
                "notes": f.notes,
                "mistake_note": f.mistake_note,
                "placed_at": f.placed_at.isoformat() if f.placed_at else None,
            }
        )
    return out


def _charges_paid(pos: Position) -> float:
    try:
        return round(sum(float(f.charges_total) for f in pos.fills.all()), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _open_pnl_net(pos: Position, ltp: float | None, gross_open: float | None) -> float | None:
    """Mark-to-market net of charges already paid on buy (+ estimated sell charges at LTP)."""
    if pos.status != Position.Status.CLOSED and ltp is not None and gross_open is not None:
        from portfolio.charges import calc_leg_charges

        units = qty_units(pos)
        product = getattr(pos.signal, "product", None) if pos.signal_id else "NRML"
        product = product or ("NRML" if is_fno(pos) else "CNC")
        sell_ch = calc_leg_charges(
            side="SELL" if pos.side == "BUY" else "BUY",
            price=float(ltp),
            units=units,
            product=product,
            is_option=is_fno(pos),
        )
        buy_charges = _charges_paid(pos)
        return round(float(gross_open) - buy_charges - sell_ch.total, 2)
    if pos.status == Position.Status.CLOSED:
        return float(pos.pnl)
    return None
