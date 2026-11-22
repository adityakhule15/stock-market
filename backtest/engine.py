from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import trading_config as cfg
from features.builders import build_dataset, feature_matrix
from features.indicators import add_indicators
from ml.train import predict_proba
from signals.engine import OrderContext, build_order, trend_ok
from signals.risk import estimate_costs


@dataclass
class Trade:
    symbol: str
    entry: float
    stop: float
    target: float
    qty: int
    exit: float
    pnl: float
    reason: str
    entry_i: int
    exit_i: int


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    start_equity: float = cfg.CAPITAL

    @property
    def expectancy_R(self) -> float:
        if not self.trades:
            return 0.0
        rs = []
        for t in self.trades:
            R = abs(t.entry - t.stop)
            if R <= 0:
                continue
            rs.append(t.pnl / (R * t.qty))
        return float(np.mean(rs)) if rs else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        eq = np.array(self.equity_curve)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        return float(abs(dd.min()) * 100)

    @property
    def hit_rate(self) -> float:
        if not self.trades:
            return 0.0
        return float(np.mean([t.pnl > 0 for t in self.trades]))


def run_backtest(
    symbol: str,
    bars: pd.DataFrame,
    bundle: dict,
    capital: float = cfg.CAPITAL,
    threshold: float = cfg.MIN_PROB,
) -> BacktestResult:
    ds = build_dataset(bars)
    result = BacktestResult(start_equity=capital)
    equity = capital
    result.equity_curve.append(equity)
    open_trade = None

    for i in range(len(ds) - 6):
        row = ds.iloc[i]
        ts = ds.index[i]
        # Locate corresponding bar index in raw bars
        try:
            bar_i = bars.index.get_loc(ts)
        except KeyError:
            continue
        if isinstance(bar_i, slice):
            continue

        if open_trade is not None:
            fut = bars.iloc[bar_i]
            # Manage open trade on this bar (conservative stop-first)
            if fut["low"] <= open_trade.stop:
                slip = open_trade.stop * (1 - cfg.SLIPPAGE_PCT)
                costs = estimate_costs(open_trade.entry * open_trade.qty, slip * open_trade.qty, cfg.PRODUCT, cfg.FEES)
                pnl = (slip - open_trade.entry) * open_trade.qty - costs
                open_trade.exit = slip
                open_trade.pnl = pnl
                open_trade.reason = "stop"
                open_trade.exit_i = bar_i
                equity += pnl
                result.trades.append(open_trade)
                open_trade = None
            elif fut["high"] >= open_trade.target:
                slip = open_trade.target * (1 - cfg.SLIPPAGE_PCT)
                costs = estimate_costs(open_trade.entry * open_trade.qty, slip * open_trade.qty, cfg.PRODUCT, cfg.FEES)
                pnl = (slip - open_trade.entry) * open_trade.qty - costs
                open_trade.exit = slip
                open_trade.pnl = pnl
                open_trade.reason = "target"
                open_trade.exit_i = bar_i
                equity += pnl
                result.trades.append(open_trade)
                open_trade = None
            result.equity_curve.append(equity)
            continue

        X, _ = feature_matrix(ds.iloc[[i]])
        prob = float(predict_proba(bundle, X)[0])
        if prob < threshold:
            result.equity_curve.append(equity)
            continue
        if not trend_ok("BUY", float(row["close"]), float(row["ema50"])):
            result.equity_curve.append(equity)
            continue
        ctx = OrderContext(
            prev_high=float(row["prev_high"]),
            prev_low=float(row["prev_low"]),
            atr=float(row["atr14"]),
            close=float(row["close"]),
            ema50=float(row["ema50"]),
        )
        order = build_order("BUY", ctx, prob)
        if order is None:
            result.equity_curve.append(equity)
            continue
        # Enter only on next bar if trigger crossed
        if bar_i + 1 >= len(bars):
            result.equity_curve.append(equity)
            continue
        nxt = bars.iloc[bar_i + 1]
        if nxt["high"] < order["entry_trigger"]:
            result.equity_curve.append(equity)
            continue
        fill = order["entry_trigger"] * (1 + cfg.SLIPPAGE_PCT)
        open_trade = Trade(
            symbol=symbol,
            entry=fill,
            stop=order["sl_trigger"],
            target=order["target1"],
            qty=order["qty"],
            exit=0.0,
            pnl=0.0,
            reason="",
            entry_i=bar_i + 1,
            exit_i=-1,
        )
        result.equity_curve.append(equity)

    if open_trade is not None:
        last = float(bars["close"].iloc[-1])
        costs = estimate_costs(open_trade.entry * open_trade.qty, last * open_trade.qty, cfg.PRODUCT, cfg.FEES)
        pnl = (last - open_trade.entry) * open_trade.qty - costs
        open_trade.exit = last
        open_trade.pnl = pnl
        open_trade.reason = "eod"
        open_trade.exit_i = len(bars) - 1
        equity += pnl
        result.trades.append(open_trade)
        result.equity_curve.append(equity)
    return result
