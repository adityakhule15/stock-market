from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import trading_config as cfg
from config.trading_config import RiskParams
from signals.risk import TICK, position_size, round_tick

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class OrderContext:
    prev_high: float
    prev_low: float
    atr: float
    close: float
    ema50: float


def build_order(
    side: str,
    ctx: OrderContext,
    prob: float,
    cfg_mod=cfg,
    risk: RiskParams | None = None,
    capital: float | None = None,
    ignore_edge_filters: bool = False,
) -> dict | None:
    """Build order ticket. When `risk` is set, use mode-aware sizing/prob thresholds.

    ignore_edge_filters=True builds watch-level tickets (still sized safely) for UI/Groww
    even when prob/RR are below the emit threshold — those must stay SKIPPED/BLOCK.
    """
    if risk is not None and risk.life_status == "dead":
        return None

    risk_pct = risk.risk_per_trade if risk else cfg_mod.RISK_PER_TRADE
    max_pos = risk.max_position_pct if risk else cfg_mod.MAX_POSITION_PCT
    min_prob = risk.min_prob if risk else cfg_mod.MIN_PROB
    cap = float(capital if capital is not None else cfg_mod.CAPITAL)

    if risk_pct <= 0 or max_pos <= 0:
        return None
    if not ignore_edge_filters and prob < min_prob:
        return None

    s = 1 if side == "BUY" else -1
    toward = "down" if s == 1 else "up"
    away = "up" if s == 1 else "down"

    # Anchor to latest close so levels match Groww (avoid stale prev_high far below market)
    if s == 1:
        base = max(float(ctx.prev_high), float(ctx.close))
    else:
        base = min(float(ctx.prev_low), float(ctx.close))
    trig = round_tick(base + s * 2 * TICK, away)
    lim = round_tick(trig * (1 + s * cfg_mod.ENTRY_BUFFER), away)
    sl_t = round_tick(trig - s * cfg_mod.ATR_SL_MULT * ctx.atr, "down" if s == 1 else "up")
    sl_l = round_tick(sl_t * (1 - s * cfg_mod.SL_BUFFER), "down" if s == 1 else "up")
    R = abs(trig - sl_t)
    if R <= 0:
        return None
    t1 = round_tick(trig + s * cfg_mod.T1_R * R, toward)
    t2 = round_tick(trig + s * cfg_mod.T2_R * R, toward)

    qty = position_size(trig, sl_t, cap, risk_pct, max_pos)
    if qty < 1:
        return None

    # Existential: shrink qty so equity - max_loss stays above DEAD_FLOOR
    max_loss_per_share = R
    max_allowed_loss = max(0.0, cap - cfg_mod.DEAD_FLOOR)
    if max_loss_per_share > 0:
        by_floor = int(max_allowed_loss // max_loss_per_share)
        qty = min(qty, by_floor)
    if qty < 1:
        return None

    from portfolio.charges import trade_economics

    econ = trade_economics(
        buy_price=trig if side == "BUY" else t1,
        sell_price=t1 if side == "BUY" else trig,
        units=qty,
        product=cfg_mod.PRODUCT,
        is_option=False,
        min_net_rs=5.0,
    )
    costs = float(econ["charges_total"])
    gross = abs(t1 - trig) * qty
    net = float(econ["net_pnl"])
    # Hard rule: never suggest a target that loses money after Groww charges
    if not ignore_edge_filters and (not econ["net_ok"] or net <= 0):
        return None
    rr_net = net / (R * qty + costs) if (R * qty + costs) > 0 else 0.0
    if not ignore_edge_filters and rr_net < cfg_mod.MIN_RR:
        return None
    return dict(
        side=side,
        entry_trigger=trig,
        entry_limit=lim,
        chase_limit=round_tick(trig + s * 0.25 * R, toward),
        sl_trigger=sl_t,
        sl_limit=sl_l,
        target1=t1,
        target2=t2,
        qty=qty,
        capital_used=round(trig * qty + float(econ["buy"]["total"]), 2),
        max_loss=round(R * qty + float(econ["buy"]["total"]), 2),
        rr_net=round(rr_net, 2),
        probability=round(prob, 3),
        product=cfg_mod.PRODUCT,
        gross_pnl=round(gross, 2),
        net_pnl=round(net, 2),
        charges_total=round(costs, 2),
        breakeven_sell=econ["breakeven_sell"],
        economics=econ,
    )


def trend_ok(side: str, close: float, ema50: float) -> bool:
    if side == "BUY":
        return close > ema50
    return close < ema50


def default_valid_until(now: datetime | None = None) -> datetime:
    now = now or datetime.now(IST)
    # Normal CNC swing: valid across multiple sessions
    return session_bounds(now)["valid_until_swing"]


def _next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _add_trading_days(d, n: int):
    out = d
    left = int(n)
    while left > 0:
        out = _next_weekday(out)
        left -= 1
    return out


def session_bounds(now: datetime | None = None) -> dict:
    """Enter/wait window dates for tickets (IST)."""
    from datetime import time as dtime

    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    d = now.date()
    if now.weekday() >= 5:
        while d.weekday() >= 5:
            d += timedelta(days=1)
    elif now.time() >= dtime(15, 30):
        d = _next_weekday(d)
    enter_after = datetime(d.year, d.month, d.day, 9, 46, tzinfo=IST)
    enter_until = datetime(d.year, d.month, d.day, 14, 0, tzinfo=IST)
    square_off = datetime(d.year, d.month, d.day, 15, 10, tzinfo=IST)
    valid_until = datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST)
    swing_end = _add_trading_days(d, 5)
    valid_until_swing = datetime(swing_end.year, swing_end.month, swing_end.day, 15, 30, tzinfo=IST)
    label = d.strftime("%d-%b-%Y")
    swing_label = swing_end.strftime("%d-%b-%Y")
    return {
        "session_date": d.isoformat(),
        "session_label": label,
        "enter_after": enter_after,
        "enter_until": enter_until,
        "square_off": square_off,
        "valid_until": valid_until,
        "valid_until_swing": valid_until_swing,
        "wait_range": f"{label} 09:46–14:00 IST (enter)",
        "hold_until": (
            f"multi-day CNC · up to {swing_label} (≤5 sessions) · exit on SL/target — "
            "net of Groww delivery charges"
        ),
        "wait_range_fno": f"{label} 09:46–14:00 IST",
        "hold_until_fno": f"{label} 15:10 IST same day only",
        "hold_days_normal": 5,
    }
