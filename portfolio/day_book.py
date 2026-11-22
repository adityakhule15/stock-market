"""Date-scoped day book — fills/closed trades with gross, charges, net.

Open rows use live mark-to-market P&L (same as Positions strip), not buy cost.
Day totals = closed realized today + open MTM for today's activity.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from portfolio.models import OrderFill, Position

IST = ZoneInfo("Asia/Kolkata")


def _d(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_day(day: date | str | None) -> date:
    if day is None:
        return timezone.localdate()
    if isinstance(day, date) and not isinstance(day, datetime):
        return day
    if isinstance(day, datetime):
        return day.astimezone(IST).date() if day.tzinfo else day.date()
    return date.fromisoformat(str(day)[:10])


def _gross_from_fills(fills: list[OrderFill], side: str = "BUY") -> float:
    """Price×units P&L before charges (long: sell turnover − buy turnover)."""
    buy_to = sum(_d(f.turnover) for f in fills if f.side == OrderFill.Side.BUY)
    sell_to = sum(_d(f.turnover) for f in fills if f.side == OrderFill.Side.SELL)
    if (side or "BUY").upper() == "SELL":
        return round(buy_to - sell_to, 2)
    return round(sell_to - buy_to, 2)


def _charges_from_fills(fills: list[OrderFill]) -> float:
    return round(sum(_d(f.charges_total) for f in fills), 2)


def _contract_meta(pos: Position, fills: list[OrderFill]) -> tuple[str, str, str]:
    sym = pos.instrument.symbol if pos.instrument_id else "?"
    contract = ""
    product = "CNC"
    for f in fills:
        if f.contract:
            contract = f.contract
        if f.product:
            product = f.product
    if not contract and pos.signal_id:
        g = getattr(pos.signal, "groww", None) or {}
        contract = g.get("contract") or g.get("display") or ""
        product = getattr(pos.signal, "product", None) or product
    return sym, contract or sym, product


def today_closed_realized(day: date | str | None = None) -> dict[str, float]:
    """Sum realized gross/charges/net for positions closed on ``day`` (IST). Excludes dummy."""
    d = _parse_day(day)
    closed = (
        Position.objects.filter(
            status=Position.Status.CLOSED, closed_at__date=d, is_dummy=False
        )
        .prefetch_related("fills")
    )
    gross = charges = net = 0.0
    n = 0
    for pos in closed:
        fills = list(pos.fills.all())
        ch = _charges_from_fills(fills)
        n_pnl = _d(pos.pnl)
        if fills:
            g = _gross_from_fills(fills, side=pos.side or "BUY")
            if abs(g - (n_pnl + ch)) > 1.0:
                g = round(n_pnl + ch, 2)
        else:
            g = n_pnl
            ch = 0.0
        gross += g
        charges += ch
        net += n_pnl
        n += 1
    return {
        "count": n,
        "gross_pnl": round(gross, 2),
        "charges_total": round(charges, 2),
        "net_pnl": round(net, 2),
    }


def _row_for_closed(pos: Position) -> dict[str, Any]:
    fills = list(pos.fills.all())
    charges = _charges_from_fills(fills)
    net = _d(pos.pnl)
    if fills:
        gross = _gross_from_fills(fills, side=pos.side or "BUY")
        if abs(gross - (net + charges)) > 1.0:
            gross = round(net + charges, 2)
    else:
        units = int(pos.qty or 0)
        entry = _d(pos.entry_price)
        exit_p = _d(pos.exit_price) if pos.exit_price is not None else entry
        if (pos.side or "BUY").upper() == "SELL":
            gross = round((entry - exit_p) * units, 2)
        else:
            gross = round((exit_p - entry) * units, 2)
        charges = 0.0

    sym, contract, product = _contract_meta(pos, fills)
    return {
        "position_id": pos.id,
        "status": pos.status,
        "symbol": sym,
        "contract": contract,
        "side": pos.side,
        "product": product,
        "qty": int(pos.qty or 0),
        "entry_price": _d(pos.entry_price),
        "exit_price": _d(pos.exit_price) if pos.exit_price is not None else None,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
        "gross_pnl": round(gross, 2),
        "charges_total": round(charges, 2),
        "net_pnl": round(net, 2),
        "ltp": None,
        "fills": [
            {
                "id": f.id,
                "side": f.side,
                "price": _d(f.price),
                "units": int(f.units or 0),
                "turnover": _d(f.turnover),
                "charges_total": _d(f.charges_total),
                "charges": f.charges or {},
                "net_cash_flow": _d(f.net_cash_flow),
                "placed_at": f.placed_at.isoformat() if f.placed_at else None,
                "source": f.source,
            }
            for f in fills
        ],
    }


def _row_for_open(pos: Position, live: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open row: live MTM gross/net (never treat buy turnover as P&L)."""
    fills = list(pos.fills.all())
    charges = _charges_from_fills(fills)
    sym, contract, product = _contract_meta(pos, fills)

    live = live or {}
    ltp = live.get("ltp")
    gross = live.get("open_pnl")
    net = live.get("open_pnl_net")

    if gross is None:
        try:
            from portfolio.live import enrich_position

            enr = enrich_position(pos)
            ltp = enr.get("ltp")
            gross = enr.get("open_pnl")
            net = enr.get("open_pnl_net")
        except Exception:  # noqa: BLE001
            gross = None
            net = None

    # No mark yet — show unknown P&L (0) rather than −invested
    if gross is None:
        gross = 0.0
    if net is None:
        # Approx: gross minus charges paid so far (no exit leg yet)
        net = round(float(gross) - charges, 2)

    return {
        "position_id": pos.id,
        "status": pos.status,
        "symbol": sym,
        "contract": contract or live.get("contract") or sym,
        "side": pos.side,
        "product": product or live.get("product") or "CNC",
        "qty": int(pos.qty or 0),
        "entry_price": _d(pos.entry_price),
        "exit_price": None,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "closed_at": None,
        "gross_pnl": round(float(gross), 2),
        "charges_total": round(charges, 2),
        "net_pnl": round(float(net), 2),
        "ltp": float(ltp) if ltp is not None else None,
        "fills": [
            {
                "id": f.id,
                "side": f.side,
                "price": _d(f.price),
                "units": int(f.units or 0),
                "turnover": _d(f.turnover),
                "charges_total": _d(f.charges_total),
                "charges": f.charges or {},
                "net_cash_flow": _d(f.net_cash_flow),
                "placed_at": f.placed_at.isoformat() if f.placed_at else None,
                "source": f.source,
            }
            for f in fills
        ],
    }


def build_day_book(
    day: date | str | None = None,
    *,
    live_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """All trades for a calendar day (IST) with live MTM on opens + day totals."""
    d = _parse_day(day)
    live_by_id = live_by_id or {}

    closed = list(
        Position.objects.filter(status=Position.Status.CLOSED, closed_at__date=d)
        .select_related("instrument", "signal")
        .prefetch_related("fills")
        .order_by("closed_at")
    )
    closed_ids = {p.id for p in closed}

    # Opens with activity today (fill or opened today)
    fill_pos_ids = set(
        OrderFill.objects.filter(placed_at__date=d, position_id__isnull=False)
        .values_list("position_id", flat=True)
        .distinct()
    )
    opened_today_ids = set(
        Position.objects.filter(status=Position.Status.OPEN, opened_at__date=d).values_list(
            "id", flat=True
        )
    )
    open_ids = (fill_pos_ids | opened_today_ids) - closed_ids
    open_today = list(
        Position.objects.filter(id__in=open_ids, status=Position.Status.OPEN)
        .select_related("instrument", "signal")
        .prefetch_related("fills")
        .order_by("opened_at")
    )

    closed_rows = [_row_for_closed(p) for p in closed]
    open_rows = [_row_for_open(p, live_by_id.get(p.id)) for p in open_today]
    for r, p in zip(closed_rows, closed):
        r["is_dummy"] = bool(p.is_dummy)
    for r, p in zip(open_rows, open_today):
        r["is_dummy"] = bool(p.is_dummy)

    orphans = list(
        OrderFill.objects.filter(placed_at__date=d, position_id__isnull=True)
        .select_related("instrument")
        .order_by("placed_at")
    )
    orphan_rows = [
        {
            "position_id": None,
            "status": "orphan_fill",
            "symbol": f.instrument.symbol if f.instrument_id else "?",
            "contract": f.contract or (f.instrument.symbol if f.instrument_id else "?"),
            "side": f.side,
            "product": f.product,
            "qty": int(f.qty or 0),
            "entry_price": _d(f.price) if f.side == OrderFill.Side.BUY else None,
            "exit_price": _d(f.price) if f.side == OrderFill.Side.SELL else None,
            "opened_at": f.placed_at.isoformat() if f.placed_at else None,
            "closed_at": None,
            "gross_pnl": 0.0,
            "charges_total": _d(f.charges_total),
            "net_pnl": _d(f.net_cash_flow),
            "ltp": None,
            "fills": [
                {
                    "id": f.id,
                    "side": f.side,
                    "price": _d(f.price),
                    "units": int(f.units or 0),
                    "turnover": _d(f.turnover),
                    "charges_total": _d(f.charges_total),
                    "charges": f.charges or {},
                    "net_cash_flow": _d(f.net_cash_flow),
                    "placed_at": f.placed_at.isoformat() if f.placed_at else None,
                    "source": f.source,
                }
            ],
        }
        for f in orphans
    ]

    # Totals exclude dummy / practice orders
    real_closed = [r for r in closed_rows if not r.get("is_dummy")]
    real_open = [r for r in open_rows if not r.get("is_dummy")]

    # All of today: closed realized + open MTM (+ orphan cash effect as net only)
    tot_gross = round(
        sum(r["gross_pnl"] for r in real_closed)
        + sum(r["gross_pnl"] for r in real_open),
        2,
    )
    tot_charges = round(
        sum(r["charges_total"] for r in real_closed)
        + sum(r["charges_total"] for r in real_open)
        + sum(r["charges_total"] for r in orphan_rows),
        2,
    )
    tot_net = round(
        sum(r["net_pnl"] for r in real_closed)
        + sum(r["net_pnl"] for r in real_open),
        2,
    )
    closed_gross = round(sum(r["gross_pnl"] for r in real_closed), 2)
    closed_net = round(sum(r["net_pnl"] for r in real_closed), 2)
    open_gross = round(sum(r["gross_pnl"] for r in real_open), 2)
    open_net = round(sum(r["net_pnl"] for r in real_open), 2)

    return {
        "date": d.isoformat(),
        "closed": closed_rows,
        "open_activity": open_rows,
        "orphan_fills": orphan_rows,
        "totals": {
            "closed_count": len(closed_rows),
            "open_count": len(open_rows),
            # Combined — all orders of this date (open MTM + closed realized)
            "gross_pnl": tot_gross,
            "charges_total": tot_charges,
            "net_pnl": tot_net,
            "you_get_after_charges": tot_net,
            "closed_gross_pnl": closed_gross,
            "closed_net_pnl": closed_net,
            "open_gross_pnl": open_gross,
            "open_net_pnl": open_net,
            "open_net_cash": open_net,
        },
    }
