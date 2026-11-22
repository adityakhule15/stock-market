from __future__ import annotations

import numpy as np
import pandas as pd

from config import trading_config as cfg


def simulate(
    bars: pd.DataFrame,
    entry: float,
    stop: float,
    target: float,
    horizon: int = 5,
    n: int = 10_000,
    seed: int = 19,
    lookback: int = 250,
) -> dict:
    """Bootstrap the stock's own candles into n possible futures. Long side."""
    pc = bars["close"].shift(1)
    rel = np.column_stack(
        [
            bars["open"] / pc - 1,
            bars["high"] / pc - 1,
            bars["low"] / pc - 1,
            bars["close"] / pc - 1,
        ]
    )[1:][-lookback:]
    rel = rel[~np.isnan(rel).any(axis=1)]
    if len(rel) < 20:
        return {"p_target": 0.0, "p_stop": 0.0, "p_neither": 1.0, "expected_R": 0.0}
    rng = np.random.default_rng(seed)
    d = rel[rng.integers(0, len(rel), size=(n, horizon))]
    p = np.full(n, float(entry))
    done = np.zeros(n, dtype=bool)
    out = np.zeros(n, dtype=int)
    for k in range(horizon):
        o, h, l, c = (p[:, None] * (1 + d[:, k, :])).T
        stop_hit = (l <= stop) & ~done
        tgt_hit = (h >= target) & ~done
        # both inside one candle -> stop first (conservative)
        both = tgt_hit & stop_hit
        out[tgt_hit & ~stop_hit] = 1
        out[stop_hit] = -1
        out[both] = -1
        done |= stop_hit | tgt_hit
        p = np.where(done, p, c)
    pt, ps = float((out == 1).mean()), float((out == -1).mean())
    r_mult = (target - entry) / (entry - stop) if (entry - stop) != 0 else 0.0
    return {
        "p_target": pt,
        "p_stop": ps,
        "p_neither": 1 - pt - ps,
        "expected_R": r_mult * pt - ps,
    }


def replay(bars: pd.DataFrame, setup_fn, levels_fn, horizon: int = 5) -> dict:
    fires = tgt = stp = 0
    for i in range(60, len(bars) - horizon):
        hist = bars.iloc[: i + 1]
        if not setup_fn(hist):
            continue
        entry, stop, target = levels_fn(hist)
        fut = bars.iloc[i + 1 : i + 1 + horizon]
        if len(fut) == 0:
            continue
        if fut["high"].iloc[0] < entry:
            continue
        fires += 1
        resolved = False
        for _, b in fut.iterrows():
            if b["low"] <= stop:
                stp += 1
                resolved = True
                break
            if b["high"] >= target:
                tgt += 1
                resolved = True
                break
        if not resolved:
            pass
    return {"fires": fires, "target": tgt, "stop": stp, "neither": fires - tgt - stp}


def verdict(sim: dict, rep: dict, cost_R: float, cfg_mod=cfg) -> dict:
    edge = sim["expected_R"] - cost_R
    hit = rep["target"] / rep["fires"] if rep["fires"] else 0.0
    if edge >= cfg_mod.TM_MIN_EDGE_R and rep["fires"] >= 15 and hit >= 0.35:
        return {"verdict": "EDGE", "expected_R_net": round(edge, 2)}
    return {
        "verdict": "NEGATIVE" if edge < -0.05 else "COIN_FLIP",
        "expected_R_net": round(edge, 2),
    }
