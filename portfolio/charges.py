"""Groww-style charge breakdown (NSE). Signals-only — never places orders.

Rates aligned to Groww F&O / equity pricing + statutory NSE charges (Apr 2026 STT).
Confirm against your Groww contract note; plans/offers can differ slightly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Side = Literal["BUY", "SELL"]
Product = Literal["CNC", "MIS", "NRML", "OPTIONS", "FUTURES"]


@dataclass
class ChargeBreakup:
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float
    total: float
    turnover: float
    side: str
    product: str
    dp: float = 0.0  # demat debit (CNC sell)

    def as_dict(self) -> dict:
        return asdict(self)


def _norm_product(product: str, *, is_option: bool = False) -> str:
    p = (product or "CNC").upper()
    if is_option or p in {"NRML", "OPT", "OPTIONS", "CE", "PE"}:
        return "OPTIONS"
    if p in {"FUT", "FUTURES"}:
        return "FUTURES"
    if p == "MIS":
        return "MIS"
    return "CNC"


def groww_brokerage(turnover: float, product: str) -> float:
    """Per executed order."""
    t = max(0.0, float(turnover))
    p = _norm_product(product)
    if p in {"OPTIONS", "FUTURES"}:
        return 20.0 if t > 0 else 0.0
    # Equity: ₹20 or 0.1% lower, min ₹5 (or 2.5% of value if smaller)
    if t <= 0:
        return 0.0
    pct = 0.001 * t
    raw = min(20.0, pct)
    floor = min(5.0, 0.025 * t)
    return round(max(raw, floor), 2)


def calc_leg_charges(
    *,
    side: Side,
    price: float,
    units: int,
    product: str = "NRML",
    is_option: bool = False,
) -> ChargeBreakup:
    """Charges for one executed leg (buy or sell)."""
    turnover = round(abs(float(price) * int(units)), 2)
    prod = _norm_product(product, is_option=is_option)
    brokerage = groww_brokerage(turnover, prod)

    stt = 0.0
    stamp = 0.0
    if prod == "CNC":
        stt = 0.001 * turnover  # delivery both sides
        if side == "BUY":
            stamp = 0.00015 * turnover
        exchange_rate = 0.0000307
    elif prod == "MIS":
        if side == "SELL":
            stt = 0.00025 * turnover
        if side == "BUY":
            stamp = 0.00003 * turnover
        exchange_rate = 0.0000307
    elif prod == "FUTURES":
        if side == "SELL":
            stt = 0.0002 * turnover  # ~0.02% sell (Groww table variants 0.02–0.05)
        if side == "BUY":
            stamp = 0.00002 * turnover
        exchange_rate = 0.0000183
    else:  # OPTIONS — premium turnover
        if side == "SELL":
            stt = 0.0015 * turnover  # 0.15% on sell premium (Apr 2026)
        if side == "BUY":
            stamp = 0.00003 * turnover  # 0.003%
        exchange_rate = 0.0003553  # ~0.03553% on premium

    exchange = exchange_rate * turnover
    sebi = 0.000001 * turnover  # ₹10 / crore
    # Groww DP on delivery sell (~₹20 + GST); waived-ish under ₹100 sale value
    dp = 0.0
    if prod == "CNC" and side == "SELL" and turnover >= 100:
        dp = round(20.0 * 1.18, 2)
    gst = 0.18 * (brokerage + exchange + sebi)
    total = round(brokerage + stt + exchange + sebi + stamp + gst + dp, 2)
    return ChargeBreakup(
        brokerage=round(brokerage, 2),
        stt=round(stt, 2),
        exchange=round(exchange, 2),
        sebi=round(sebi, 4),
        stamp=round(stamp, 2),
        gst=round(gst, 2),
        total=total,
        turnover=turnover,
        side=side,
        product=prod,
        dp=dp,
    )


def round_trip_charges(
    *,
    buy_price: float,
    sell_price: float,
    units: int,
    product: str = "NRML",
    is_option: bool = False,
) -> dict:
    buy = calc_leg_charges(side="BUY", price=buy_price, units=units, product=product, is_option=is_option)
    sell = calc_leg_charges(side="SELL", price=sell_price, units=units, product=product, is_option=is_option)
    gross = round((float(sell_price) - float(buy_price)) * int(units), 2)
    net = round(gross - buy.total - sell.total, 2)
    return {
        "buy": buy.as_dict(),
        "sell": sell.as_dict(),
        "charges_total": round(buy.total + sell.total, 2),
        "gross_pnl": gross,
        "net_pnl": net,
    }


def trade_economics(
    *,
    buy_price: float,
    sell_price: float,
    units: int,
    product: str = "CNC",
    is_option: bool = False,
    min_net_rs: float = 1.0,
) -> dict:
    """Full suggestion economics — never recommend a net-loss target after Groww charges."""
    units = max(1, int(units))
    buy_p = float(buy_price)
    sell_p = float(sell_price)
    rt = round_trip_charges(
        buy_price=buy_p, sell_price=sell_p, units=units, product=product, is_option=is_option
    )
    buy_debit = round(rt["buy"]["turnover"] + rt["buy"]["total"], 2)
    # Breakeven sell LTP (binary search — charges depend slightly on sell turnover)
    lo, hi = buy_p, max(buy_p * 3, buy_p + 500)
    be = buy_p
    for _ in range(40):
        mid = (lo + hi) / 2
        trial = round_trip_charges(
            buy_price=buy_p, sell_price=mid, units=units, product=product, is_option=is_option
        )
        if trial["net_pnl"] >= 0:
            be = mid
            hi = mid
        else:
            lo = mid
    be = round(be + 0.05, 2)  # tick cushion
    net = float(rt["net_pnl"])
    gross = float(rt["gross_pnl"])
    verdict = "NET_PROFIT" if net >= float(min_net_rs) else ("BREAKEVEN" if net >= 0 else "NET_LOSS")
    tip = ""
    if verdict == "NET_LOSS":
        tip = (
            f"Buy ₹{buy_p:.2f} → sell ₹{sell_p:.2f} is a LOSS after Groww charges "
            f"(gross ₹{gross:.2f} − charges ₹{rt['charges_total']:.2f} = net ₹{net:.2f}). "
            f"Need sell ≥ ₹{be:.2f} to break even."
        )
    elif verdict == "BREAKEVEN":
        tip = f"Barely covers Groww charges — net ₹{net:.2f}. Prefer stronger target."
    else:
        tip = (
            f"Net +₹{net:.2f} after Groww brokerage/STT/GST/etc "
            f"(gross ₹{gross:.2f} − charges ₹{rt['charges_total']:.2f})."
        )
    return {
        **rt,
        "buy_debit_with_charges": buy_debit,
        "breakeven_sell": be,
        "net_ok": verdict == "NET_PROFIT",
        "verdict": verdict,
        "tip": tip,
        "units": units,
        "product": _norm_product(product, is_option=is_option),
    }


# Keep engine estimate_costs compatible via thin wrapper
def estimate_costs_groww(buy_value: float, sell_value: float, product: str, is_option: bool = False) -> float:
    units = 1
    # values given as turnover already
    buy_price = float(buy_value)
    sell_price = float(sell_value)
    # treat as price*units with units=1 when callers pass turnover
    buy = calc_leg_charges(side="BUY", price=buy_price, units=units, product=product, is_option=is_option)
    sell = calc_leg_charges(side="SELL", price=sell_price, units=units, product=product, is_option=is_option)
    # Overestimate if buy_value was already turnover — callers use price*qty as value.
    # Recompute properly when value is turnover: set price=value, units=1 → OK for total fees scale.
    return round(buy.total + sell.total, 2)
