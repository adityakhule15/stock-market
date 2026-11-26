"""Index chart / trading-technique pattern detectors (NIFTY, BANKNIFTY)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from features.indicators import atr, ema, rsi


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _session_slice(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    idx = df.index
    if hasattr(idx, "tz") and idx.tz is not None:
        local = idx.tz_convert("Asia/Kolkata")
    else:
        local = idx
    today = pd.Timestamp(local[-1]).date()
    mask = pd.Series([pd.Timestamp(t).date() == today for t in local], index=df.index)
    return df.loc[mask]


def detect_index_patterns(
    *,
    daily: pd.DataFrame | None = None,
    bars_5m: pd.DataFrame | None = None,
    bars_1m: pd.DataFrame | None = None,
    spot: float | None = None,
) -> list[dict[str, Any]]:
    """Return active pattern hits with heuristic confidence 0–1."""
    hits: list[dict[str, Any]] = []
    d = daily.copy() if daily is not None and not daily.empty else None
    m5 = bars_5m.copy() if bars_5m is not None and not bars_5m.empty else None
    m1 = bars_1m.copy() if bars_1m is not None and not bars_1m.empty else None

    last_close = spot
    if last_close is None or last_close <= 0:
        for frame in (m1, m5, d):
            if frame is not None and not frame.empty:
                last_close = _safe_float(frame["close"].iloc[-1])
                break
    last_close = _safe_float(last_close)

    # --- Daily EMA stack ---
    if d is not None and len(d) >= 60:
        c = d["close"].astype(float)
        e9, e20, e50 = ema(c, 9), ema(c, 20), ema(c, 50)
        if e9.iloc[-1] > e20.iloc[-1] > e50.iloc[-1] and c.iloc[-1] > e9.iloc[-1]:
            hits.append(
                {
                    "name": "ema_stack_bull",
                    "side": "BULLISH",
                    "confidence": 0.62,
                    "note": "EMA9 > EMA20 > EMA50 with price above EMA9",
                }
            )
        elif e9.iloc[-1] < e20.iloc[-1] < e50.iloc[-1] and c.iloc[-1] < e9.iloc[-1]:
            hits.append(
                {
                    "name": "ema_stack_bear",
                    "side": "BEARISH",
                    "confidence": 0.62,
                    "note": "EMA9 < EMA20 < EMA50 with price below EMA9",
                }
            )

        # Engulfing on last daily bar
        if len(d) >= 3:
            o1, c1 = float(d["open"].iloc[-2]), float(d["close"].iloc[-2])
            o0, c0 = float(d["open"].iloc[-1]), float(d["close"].iloc[-1])
            if c0 > o0 and o0 <= c1 and c0 >= o1 and (c0 - o0) > abs(c1 - o1):
                hits.append(
                    {
                        "name": "bull_engulf",
                        "side": "BULLISH",
                        "confidence": 0.58,
                        "note": "Daily bullish engulfing",
                    }
                )
            if c0 < o0 and o0 >= c1 and c0 <= o1 and (o0 - c0) > abs(c1 - o1):
                hits.append(
                    {
                        "name": "bear_engulf",
                        "side": "BEARISH",
                        "confidence": 0.58,
                        "note": "Daily bearish engulfing",
                    }
                )

        # RSI extremes
        r = rsi(c, 14)
        r_y, r_t = float(r.iloc[-2]), float(r.iloc[-1])
        if r_y < 30 and r_t > r_y and c.iloc[-1] > c.iloc[-2]:
            hits.append(
                {
                    "name": "rsi_oversold_bounce",
                    "side": "BULLISH",
                    "confidence": 0.55,
                    "note": f"RSI recovered from {r_y:.0f}",
                }
            )
        if r_y > 70 and r_t < r_y and c.iloc[-1] < c.iloc[-2]:
            hits.append(
                {
                    "name": "rsi_overbought_fade",
                    "side": "BEARISH",
                    "confidence": 0.55,
                    "note": f"RSI rolled from {r_y:.0f}",
                }
            )

        # Inside bar / NR7 break
        if len(d) >= 10:
            ranges = (d["high"] - d["low"]).astype(float)
            prev_inside = (
                float(d["high"].iloc[-2]) <= float(d["high"].iloc[-3])
                and float(d["low"].iloc[-2]) >= float(d["low"].iloc[-3])
            )
            nr7 = float(ranges.iloc[-2]) <= float(ranges.iloc[-8:-1].min())
            broke_up = last_close > float(d["high"].iloc[-2])
            broke_dn = last_close < float(d["low"].iloc[-2])
            if prev_inside and broke_up:
                hits.append(
                    {
                        "name": "inside_bar_break",
                        "side": "BULLISH",
                        "confidence": 0.57,
                        "note": "Break above yesterday inside bar",
                    }
                )
            if prev_inside and broke_dn:
                hits.append(
                    {
                        "name": "inside_bar_break",
                        "side": "BEARISH",
                        "confidence": 0.57,
                        "note": "Break below yesterday inside bar",
                    }
                )
            if nr7 and broke_up:
                hits.append(
                    {
                        "name": "nr7_break",
                        "side": "BULLISH",
                        "confidence": 0.56,
                        "note": "NR7 expansion up",
                    }
                )
            if nr7 and broke_dn:
                hits.append(
                    {
                        "name": "nr7_break",
                        "side": "BEARISH",
                        "confidence": 0.56,
                        "note": "NR7 expansion down",
                    }
                )

    # --- Intraday ORB + VWAP on 5m ---
    day5 = _session_slice(m5) if m5 is not None else pd.DataFrame()
    if not day5.empty and len(day5) >= 4:
        # Opening range ≈ first 3×5m bars (09:15–09:29)
        or_bars = day5.iloc[:3]
        or_high = float(or_bars["high"].max())
        or_low = float(or_bars["low"].min())
        px = last_close or float(day5["close"].iloc[-1])
        if px > or_high:
            hits.append(
                {
                    "name": "orb_break",
                    "side": "BULLISH",
                    "confidence": 0.64,
                    "note": f"Above OR high {or_high:.1f}",
                    "or_high": or_high,
                    "or_low": or_low,
                }
            )
        elif px < or_low:
            hits.append(
                {
                    "name": "orb_break",
                    "side": "BEARISH",
                    "confidence": 0.64,
                    "note": f"Below OR low {or_low:.1f}",
                    "or_high": or_high,
                    "or_low": or_low,
                }
            )

        # Session VWAP reclaim / reject
        typ = (day5["high"] + day5["low"] + day5["close"]) / 3.0
        vol = day5["volume"].astype(float).clip(lower=1.0)
        vwap = (typ * vol).cumsum() / vol.cumsum()
        v_now = float(vwap.iloc[-1])
        v_prev = float(vwap.iloc[-2]) if len(vwap) > 1 else v_now
        c_now = float(day5["close"].iloc[-1])
        c_prev = float(day5["close"].iloc[-2]) if len(day5) > 1 else c_now
        if c_prev < v_prev and c_now > v_now:
            hits.append(
                {
                    "name": "vwap_reclaim",
                    "side": "BULLISH",
                    "confidence": 0.60,
                    "note": f"Reclaimed VWAP {v_now:.1f}",
                    "vwap": v_now,
                }
            )
        if c_prev > v_prev and c_now < v_now:
            hits.append(
                {
                    "name": "vwap_reject",
                    "side": "BEARISH",
                    "confidence": 0.60,
                    "note": f"Lost VWAP {v_now:.1f}",
                    "vwap": v_now,
                }
            )

    # Dedupe by name keeping higher confidence
    best: dict[str, dict[str, Any]] = {}
    for h in hits:
        key = f"{h['name']}:{h['side']}"
        if key not in best or float(h.get("confidence") or 0) > float(best[key].get("confidence") or 0):
            best[key] = h
    out = list(best.values())
    out.sort(key=lambda x: float(x.get("confidence") or 0), reverse=True)
    return out


def pattern_feature_flags(hits: list[dict[str, Any]]) -> dict[str, float]:
    """Binary/confidence features for the ML row."""
    from indexdesk.constants import PATTERN_NAMES

    flags = {f"pat_{n}": 0.0 for n in PATTERN_NAMES}
    flags["pat_bull_count"] = 0.0
    flags["pat_bear_count"] = 0.0
    flags["pat_max_conf"] = 0.0
    for h in hits:
        name = h.get("name") or ""
        conf = float(h.get("confidence") or 0)
        key = f"pat_{name}"
        if key in flags:
            flags[key] = max(flags[key], conf)
        side = (h.get("side") or "").upper()
        if side == "BULLISH":
            flags["pat_bull_count"] += 1.0
        elif side == "BEARISH":
            flags["pat_bear_count"] += 1.0
        flags["pat_max_conf"] = max(flags["pat_max_conf"], conf)
    return flags


def bias_from_patterns(hits: list[dict[str, Any]], model_prob_up: float | None = None) -> dict[str, Any]:
    """Combine pattern votes (+ optional model) into bias + percentages."""
    bull = sum(float(h.get("confidence") or 0) for h in hits if (h.get("side") or "").upper() == "BULLISH")
    bear = sum(float(h.get("confidence") or 0) for h in hits if (h.get("side") or "").upper() == "BEARISH")
    total = bull + bear
    if total <= 1e-9:
        pat_up = 0.50
    else:
        pat_up = bull / total

    if model_prob_up is not None and 0.0 <= float(model_prob_up) <= 1.0:
        # Blend: model 55%, patterns 45%
        p_up = 0.55 * float(model_prob_up) + 0.45 * pat_up
    else:
        p_up = pat_up

    p_up = float(np.clip(p_up, 0.05, 0.95))
    p_dn = 1.0 - p_up
    if p_up >= 0.58:
        bias = "BULLISH"
    elif p_dn >= 0.58:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"
    return {
        "bias": bias,
        "prob_up_pct": round(p_up * 100, 1),
        "prob_down_pct": round(p_dn * 100, 1),
        "pattern_bull_score": round(bull, 3),
        "pattern_bear_score": round(bear, 3),
        "model_prob_up": model_prob_up,
    }
