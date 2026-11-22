"""Walk-forward backtest using the shared ticket simulator + charges."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest.simulator import SimLevels, simulate_ticket
from config import trading_config as cfg
from features.builders import build_dataset, feature_matrix
from ml.train import fit_lgbm, predict_proba, time_train_test_split
from signals.engine import OrderContext, build_order, trend_ok


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
    charges: float = 0.0
    net_pnl: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    start_equity: float = cfg.CAPITAL
    nifty_return: float | None = None
    strategy_return: float | None = None
    metrics: dict = field(default_factory=dict)

    @property
    def expectancy_R(self) -> float:
        if not self.trades:
            return 0.0
        rs = []
        for t in self.trades:
            R = abs(t.entry - t.stop)
            if R <= 0:
                continue
            rs.append(t.net_pnl / (R * t.qty))
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
        return float(np.mean([t.net_pnl > 0 for t in self.trades]))


def run_backtest(
    symbol: str,
    bars: pd.DataFrame,
    bundle: dict | None = None,
    capital: float = cfg.CAPITAL,
    threshold: float = cfg.MIN_PROB,
    *,
    walk_forward: bool = True,
) -> BacktestResult:
    """1 position at a time, 1 trade/day, wallet sizing, simulator fills + charges."""
    result = BacktestResult(start_equity=capital)
    equity = capital
    result.equity_curve.append(equity)

    ds = build_dataset(bars, include_market=False)
    if ds.empty:
        return result

    # Walk-forward: train only on data before each test window
    if walk_forward and bundle is None and len(ds) > 200:
        train_df, test_df = time_train_test_split(ds, test_ratio=0.35, embargo=8)
        X_tr, y_tr = feature_matrix(train_df)
        if y_tr.nunique() > 1 and len(X_tr) >= 80:
            bundle = fit_lgbm(X_tr, y_tr)
            thr = float(bundle.get("decision_threshold") or threshold)
        else:
            thr = threshold
        eval_ds = test_df
    else:
        thr = float((bundle or {}).get("decision_threshold") or threshold)
        eval_ds = ds
        if bundle is None:
            # Require a model — train on early half without scoring it as holdout
            train_df, _ = time_train_test_split(ds, test_ratio=0.3, embargo=8)
            X_tr, y_tr = feature_matrix(train_df)
            if y_tr.nunique() > 1:
                bundle = fit_lgbm(X_tr, y_tr)

    if bundle is None:
        return result

    last_trade_date = None
    open_until = -1

    for i in range(len(eval_ds) - 6):
        row = eval_ds.iloc[i]
        ts = eval_ds.index[i]
        try:
            bar_i = bars.index.get_loc(ts)
        except KeyError:
            continue
        if isinstance(bar_i, slice):
            continue
        if bar_i <= open_until:
            continue

        day = ts.date() if hasattr(ts, "date") else None
        if last_trade_date is not None and day == last_trade_date:
            continue

        X = feature_matrix(eval_ds.iloc[[i]])[0]
        try:
            prob = float(predict_proba(bundle, X)[0])
        except Exception:  # noqa: BLE001
            continue
        if prob < thr:
            continue
        close = float(row["close"])
        atr = float(row["atr14"])
        ema50 = float(row.get("ema50") or close)
        if not trend_ok(close, ema50, "BUY"):
            continue
        ctx = OrderContext(
            prev_high=max(float(row.get("prev_high") or close), close),
            prev_low=min(float(row.get("prev_low") or close), close),
            atr=atr,
            close=close,
            ema50=ema50,
        )
        order = build_order(ctx, "BUY", equity, prob, ignore_edge_filters=False)
        if not order:
            continue
        levels = SimLevels(
            entry_trigger=float(order["entry_trigger"]),
            stop=float(order["sl_trigger"]),
            target=float(order["target1"]),
            atr=atr,
            signal_i=bar_i,
        )
        res = simulate_ticket(
            bars,
            levels,
            qty=int(order["qty"]),
            horizon=5,
            apply_management=True,
        )
        if not res.triggered:
            continue
        trade = Trade(
            symbol=symbol,
            entry=res.entry_fill,
            stop=float(order["sl_trigger"]),
            target=float(order["target1"]),
            qty=res.qty,
            exit=res.exit_fill,
            pnl=res.gross_pnl,
            reason=res.exit_reason,
            entry_i=res.entry_i,
            exit_i=res.exit_i,
            charges=res.charges,
            net_pnl=res.net_pnl,
        )
        equity += res.net_pnl
        result.trades.append(trade)
        result.equity_curve.append(equity)
        last_trade_date = day
        open_until = res.exit_i

    result.strategy_return = (equity / capital - 1.0) if capital else 0.0
    # NIFTY buy-and-hold over same window
    try:
        from marketdata.service import MarketDataService

        nifty, _ = MarketDataService().history("^NSEI", interval="1d", period="5y")
        if nifty is not None and len(nifty) > 2 and len(eval_ds):
            start_d = eval_ds.index.min()
            end_d = eval_ds.index.max()
            n = nifty.loc[(nifty.index >= start_d) & (nifty.index <= end_d)]
            if len(n) > 1:
                result.nifty_return = float(n["close"].iloc[-1] / n["close"].iloc[0] - 1.0)
    except Exception:  # noqa: BLE001
        pass
    result.metrics = {
        "n_trades": len(result.trades),
        "hit_rate": result.hit_rate,
        "expectancy_R": result.expectancy_R,
        "max_dd_pct": result.max_drawdown_pct,
        "strategy_return": result.strategy_return,
        "nifty_return": result.nifty_return,
        "end_equity": equity,
    }
    return result
