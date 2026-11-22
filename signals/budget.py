"""Budget stage — size to wallet, max loss, Groww charges, net reward."""

from __future__ import annotations

from typing import Any

from config import trading_config as cfg
from portfolio.charges import trade_economics
from signals.risk import position_size


def budget_stage(
    *,
    entry: float,
    stop: float,
    target: float,
    side: str = "BUY",
    product: str = "CNC",
    is_option: bool = False,
    lot_size: int = 1,
    lots: int | None = None,
    capital: float | None = None,
    risk_pct: float | None = None,
    max_position_pct: float | None = None,
    equity: float | None = None,
    cfg_mod=cfg,
) -> dict[str, Any]:
    capital = float(capital if capital is not None else cfg_mod.CAPITAL)
    equity = float(equity if equity is not None else capital)
    risk_pct = float(risk_pct if risk_pct is not None else cfg_mod.RISK_PER_TRADE)
    max_position_pct = float(
        max_position_pct if max_position_pct is not None else cfg_mod.MAX_POSITION_PCT
    )

    affordable_lots = 0
    if is_option:
        premium = entry
        unit_cost = float(premium) * int(lot_size) if lot_size else 0.0
        affordable_lots = int(capital // unit_cost) if unit_cost > 0 else 0
        if lots is None:
            # 1 lot if premium*lot fits wallet (display-only: affordable_lots may be higher)
            cost = premium * lot_size
            if cost > capital:
                return {
                    "ok": False,
                    "fno_fits": False,
                    "qty": 0,
                    "lots": 0,
                    "affordable_lots": 0,
                    "reason": "no F&O lot fits the wallet — show Normal ideas only",
                    "rupees_at_risk": 0.0,
                    "charges_total": 0.0,
                    "net_reward": 0.0,
                    "net_ok": False,
                }
            lots = 1
        units = int(lots) * int(lot_size)
        qty = int(lots)
        max_loss = abs(entry - stop) * units
        capital_used = entry * units
    else:
        qty = position_size(entry, stop, capital, risk_pct, max_position_pct)
        if qty < 1:
            return {
                "ok": False,
                "fno_fits": True,
                "qty": 0,
                "lots": 0,
                "affordable_lots": 0,
                "reason": "size below 1 share at max risk",
                "rupees_at_risk": 0.0,
                "charges_total": 0.0,
                "net_reward": 0.0,
                "net_ok": False,
            }
        units = qty
        max_loss = abs(entry - stop) * units
        capital_used = entry * units
        lots = 0

    # Never size a trade that can breach DEAD floor
    if equity - max_loss < cfg_mod.DEAD_FLOOR - cfg_mod.MAX_LOSS_FLOOR_BUFFER:
        return {
            "ok": False,
            "fno_fits": not is_option or qty > 0,
            "qty": 0,
            "lots": 0,
            "affordable_lots": affordable_lots if is_option else 0,
            "reason": "max loss would breach DEAD floor — blocked",
            "rupees_at_risk": max_loss,
            "charges_total": 0.0,
            "net_reward": 0.0,
            "net_ok": False,
        }

    # Next floor (SURVIVAL band → DEAD, ALIVE → SURVIVAL)
    next_floor = cfg_mod.DEAD_FLOOR if equity <= cfg_mod.SURVIVAL_EQUITY else cfg_mod.SURVIVAL_EQUITY
    if equity > next_floor and equity - max_loss < next_floor - cfg_mod.MAX_LOSS_FLOOR_BUFFER:
        return {
            "ok": False,
            "fno_fits": not is_option or qty > 0,
            "qty": 0,
            "lots": 0,
            "affordable_lots": affordable_lots if is_option else 0,
            "reason": f"max loss would breach next floor ₹{next_floor:.0f}",
            "rupees_at_risk": max_loss,
            "charges_total": 0.0,
            "net_reward": 0.0,
            "net_ok": False,
        }

    eco = trade_economics(
        buy_price=entry if side == "BUY" else target,
        sell_price=target if side == "BUY" else entry,
        units=units,
        product=product,
        is_option=is_option,
    )
    hint = ""
    if is_option and affordable_lots > 1:
        hint = f" · wallet can afford {affordable_lots} lots"
    return {
        "ok": bool(eco.get("net_ok", False)) and qty >= 1,
        "fno_fits": True,
        "qty": qty,
        "lots": lots if is_option else 0,
        "affordable_lots": affordable_lots if is_option else 0,
        "units": units,
        "capital_used": round(capital_used, 2),
        "rupees_at_risk": round(max_loss, 2),
        "max_loss": round(max_loss, 2),
        "charges_total": round(float(eco.get("charges_total", 0)), 2),
        "net_reward": round(float(eco.get("net_pnl", 0)), 2),
        "net_ok": bool(eco.get("net_ok", False)),
        "breakeven_sell": eco.get("breakeven_sell"),
        "tip": eco.get("tip", ""),
        "rr_net": (float(eco.get("net_pnl", 0)) / max_loss) if max_loss > 0 else 0.0,
        "reason": (
            f"Recommended: {qty} {'lot' if is_option and qty == 1 else ('lots' if is_option else 'shares')}"
            f"{hint}; "
            f"₹{max_loss:.0f} at risk; net ₹{float(eco.get('net_pnl', 0)):.0f} after Groww charges"
        ),
        "economics": eco,
    }
