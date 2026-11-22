"""Shared ticket simulator — same rules for labels, Time Machine, backtest, shadow book.

Entry: max(prev_high, close) + 2 ticks on the *next* session.
Gap past trigger → fill at open. Stop-first if both hit in one bar.
Optional live management: EXIT at -0.5R, move SL to entry at +1R.
Charges via trade_economics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from config import trading_config as cfg
from signals.risk import TICK, round_tick

ExitReason = Literal["target", "stop", "time", "adverse", "no_trigger", "open"]


@dataclass
class SimLevels:
    entry_trigger: float
    stop: float
    target: float
    atr: float
    signal_i: int  # bar index where decision is made (close of day)


@dataclass
class SimResult:
    triggered: bool
    entry_fill: float
    exit_fill: float
    exit_reason: ExitReason
    entry_i: int
    exit_i: int
    qty: int
    gross_pnl: float
    charges: float
    net_pnl: float
    hit_target: bool  # True only if target before stop (label=1)


def build_levels_from_bar(
    bars: pd.DataFrame,
    i: int,
    *,
    atr_mult_sl: float | None = None,
    t1_r: float | None = None,
    side: str = "BUY",
) -> SimLevels | None:
    """Levels as of bar i close (decision bar). Entry is for next session."""
    if i < 1 or i >= len(bars) - 1:
        return None
    atr_mult_sl = cfg.ATR_SL_MULT if atr_mult_sl is None else atr_mult_sl
    t1_r = cfg.T1_R if t1_r is None else t1_r
    row = bars.iloc[i]
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    # ATR from history up to i
    hist = bars.iloc[: i + 1]
    if "atr14" in hist.columns and not pd.isna(hist["atr14"].iloc[-1]):
        atr = float(hist["atr14"].iloc[-1])
    else:
        from features.indicators import atr as atr_fn

        a = atr_fn(hist)
        atr = float(a.iloc[-1]) if len(a) and not pd.isna(a.iloc[-1]) else 0.0
    if atr <= 0:
        return None
    s = 1 if side == "BUY" else -1
    if s == 1:
        base = max(high, close)
        trig = round_tick(base + 2 * TICK, "up")
        stop = round_tick(trig - atr_mult_sl * atr, "down")
    else:
        base = min(low, close)
        trig = round_tick(base - 2 * TICK, "down")
        stop = round_tick(trig + atr_mult_sl * atr, "up")
    R = abs(trig - stop)
    if R <= 0:
        return None
    target = round_tick(trig + s * t1_r * R, "up" if s == 1 else "down")
    return SimLevels(entry_trigger=trig, stop=stop, target=target, atr=atr, signal_i=i)


def simulate_ticket(
    bars: pd.DataFrame,
    levels: SimLevels,
    *,
    qty: int = 1,
    horizon: int = 5,
    side: str = "BUY",
    product: str = "CNC",
    apply_management: bool = True,
    adverse_r: float | None = None,
    slippage_pct: float | None = None,
) -> SimResult:
    """Walk forward from signal_i+1 for up to `horizon` sessions."""
    adverse_r = cfg.ADVERSE_R_EXIT if adverse_r is None else adverse_r
    slip = cfg.SLIPPAGE_PCT if slippage_pct is None else float(slippage_pct)
    qty = max(1, int(qty))
    start = levels.signal_i + 1
    end = min(len(bars), start + horizon)
    if start >= len(bars):
        return _empty(levels, qty, "no_trigger")

    trig = float(levels.entry_trigger)
    stop = float(levels.stop)
    target = float(levels.target)
    entry_fill = 0.0
    entry_i = -1
    s = 1 if side == "BUY" else -1

    # Find entry
    for j in range(start, end):
        bar = bars.iloc[j]
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if s == 1:
            if h < trig:
                continue
            # Gap through trigger → fill at open if open already above
            fill = o if o >= trig else trig
            fill = fill * (1 + slip)
        else:
            if l > trig:
                continue
            fill = o if o <= trig else trig
            fill = fill * (1 - slip)
        entry_fill = round(fill, 4)
        entry_i = j
        break

    if entry_i < 0:
        return _empty(levels, qty, "no_trigger")

    R = abs(entry_fill - stop) if abs(entry_fill - stop) > 0 else abs(trig - stop)
    cur_stop = stop
    # From entry bar onward (same bar can hit stop/target after fill — conservative)
    for j in range(entry_i, end):
        bar = bars.iloc[j]
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        # Mark for adverse / trail uses close mid-path approximation
        mark = c
        if apply_management and R > 0:
            r_mult = (mark - entry_fill) / R if s == 1 else (entry_fill - mark) / R
            if r_mult <= adverse_r:
                exit_fill = mark * (1 - s * slip)
                return _settle(entry_fill, exit_fill, qty, product, "adverse", entry_i, j, target_first=False)
            if r_mult >= 1.0:
                # Move stop to entry (breakeven) — tighten only
                if s == 1:
                    cur_stop = max(cur_stop, entry_fill)
                else:
                    cur_stop = min(cur_stop, entry_fill)

        # Stop / target — stop first if both
        if s == 1:
            stop_hit = l <= cur_stop
            tgt_hit = h >= target
            if stop_hit and tgt_hit:
                exit_fill = cur_stop * (1 - slip)
                return _settle(entry_fill, exit_fill, qty, product, "stop", entry_i, j, target_first=False)
            if stop_hit:
                # Gap through stop → open if open already below
                fill = o if o <= cur_stop else cur_stop
                exit_fill = fill * (1 - slip)
                return _settle(entry_fill, exit_fill, qty, product, "stop", entry_i, j, target_first=False)
            if tgt_hit:
                fill = o if o >= target else target
                exit_fill = fill * (1 - slip)
                return _settle(entry_fill, exit_fill, qty, product, "target", entry_i, j, target_first=True)
        else:
            stop_hit = h >= cur_stop
            tgt_hit = l <= target
            if stop_hit and tgt_hit:
                exit_fill = cur_stop * (1 + slip)
                return _settle(entry_fill, exit_fill, qty, product, "stop", entry_i, j, target_first=False)
            if stop_hit:
                fill = o if o >= cur_stop else cur_stop
                exit_fill = fill * (1 + slip)
                return _settle(entry_fill, exit_fill, qty, product, "stop", entry_i, j, target_first=False)
            if tgt_hit:
                fill = o if o <= target else target
                exit_fill = fill * (1 + slip)
                return _settle(entry_fill, exit_fill, qty, product, "target", entry_i, j, target_first=True)

    # Time exit at last close in horizon
    last_i = end - 1
    last_close = float(bars.iloc[last_i]["close"])
    exit_fill = last_close * (1 - s * slip)
    return _settle(entry_fill, exit_fill, qty, product, "time", entry_i, last_i, target_first=False)


def _empty(levels: SimLevels, qty: int, reason: ExitReason) -> SimResult:
    return SimResult(
        triggered=False,
        entry_fill=0.0,
        exit_fill=0.0,
        exit_reason=reason,
        entry_i=-1,
        exit_i=-1,
        qty=qty,
        gross_pnl=0.0,
        charges=0.0,
        net_pnl=0.0,
        hit_target=False,
    )


def _settle(
    entry: float,
    exit_: float,
    qty: int,
    product: str,
    reason: ExitReason,
    entry_i: int,
    exit_i: int,
    *,
    target_first: bool,
) -> SimResult:
    from portfolio.charges import trade_economics

    buy_p, sell_p = (entry, exit_) if exit_ >= entry or reason != "short" else (exit_, entry)
    # Long only path for CNC: buy at entry, sell at exit
    eco = trade_economics(
        buy_price=entry,
        sell_price=exit_,
        units=qty,
        product=product,
        is_option=False,
        min_net_rs=0.0,
    )
    return SimResult(
        triggered=True,
        entry_fill=round(entry, 4),
        exit_fill=round(exit_, 4),
        exit_reason=reason,
        entry_i=entry_i,
        exit_i=exit_i,
        qty=qty,
        gross_pnl=float(eco["gross_pnl"]),
        charges=float(eco["charges_total"]),
        net_pnl=float(eco["net_pnl"]),
        hit_target=bool(target_first),
    )


def label_series(
    bars: pd.DataFrame,
    *,
    atr_mult_sl: float | None = None,
    t1_r: float | None = None,
    horizon: int = 5,
    apply_management: bool = True,
) -> pd.Series:
    """1 if ticket hits target before stop (and entry triggers), else 0. NaN near end."""
    from features.indicators import add_indicators

    ind = add_indicators(bars)
    work = bars.copy()
    if "atr14" not in work.columns:
        work["atr14"] = ind["atr14"]
    n = len(work)
    labels = np.full(n, np.nan)
    for i in range(n - horizon - 1):
        levels = build_levels_from_bar(work, i, atr_mult_sl=atr_mult_sl, t1_r=t1_r)
        if levels is None:
            continue
        res = simulate_ticket(
            work,
            levels,
            qty=1,
            horizon=horizon,
            apply_management=apply_management,
        )
        if not res.triggered:
            labels[i] = 0.0
        else:
            labels[i] = 1.0 if res.hit_target else 0.0
    return pd.Series(labels, index=bars.index, name="y")


def as_dict(res: SimResult) -> dict[str, Any]:
    return {
        "triggered": res.triggered,
        "entry_fill": res.entry_fill,
        "exit_fill": res.exit_fill,
        "exit_reason": res.exit_reason,
        "entry_i": res.entry_i,
        "exit_i": res.exit_i,
        "qty": res.qty,
        "gross_pnl": res.gross_pnl,
        "charges": res.charges,
        "net_pnl": res.net_pnl,
        "hit_target": res.hit_target,
    }
