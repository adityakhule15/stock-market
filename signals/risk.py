from __future__ import annotations

import math

TICK = 0.05


def round_tick(price: float, direction: str = "nearest") -> float:
    t = price / TICK
    if direction == "up":
        t = math.ceil(t - 1e-9)
    elif direction == "down":
        t = math.floor(t + 1e-9)
    else:
        t = round(t)
    return round(t * TICK, 2)


def position_size(entry, stop, capital, risk_pct, max_pos_pct) -> int:
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return 0
    by_risk = math.floor(capital * risk_pct / risk_per_share)
    by_cap = math.floor(capital * max_pos_pct / entry)
    return max(0, min(by_risk, by_cap))


def estimate_costs(buy_value, sell_value, product, f) -> float:
    """Groww-style round-trip charges (buy_value/sell_value = turnover ₹)."""
    try:
        from portfolio.charges import calc_leg_charges

        is_opt = str(product or "").upper() in {"NRML", "OPT", "OPTIONS"}
        buy = calc_leg_charges(
            side="BUY", price=float(buy_value), units=1, product=str(product or "CNC"), is_option=is_opt
        )
        sell = calc_leg_charges(
            side="SELL", price=float(sell_value), units=1, product=str(product or "CNC"), is_option=is_opt
        )
        return float(buy.total + sell.total)
    except Exception:  # noqa: BLE001
        brokerage = f.brokerage(buy_value, product) + f.brokerage(sell_value, product)
        if product == "CNC":
            stt = f.STT_DELIVERY * (buy_value + sell_value)
            stamp = f.STAMP_DELIVERY * buy_value
        else:
            stt = f.STT_INTRADAY * sell_value
            stamp = f.STAMP_INTRADAY * buy_value
        exch = f.EXCHANGE_TXN * (buy_value + sell_value)
        sebi = f.SEBI * (buy_value + sell_value)
        gst = f.GST * (brokerage + exch + sebi)
        return brokerage + stt + exch + sebi + stamp + gst
