"""Pure stats over Kronos sample paths — no torch, safe for Django/tests."""

from __future__ import annotations

from typing import Any


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def path_outcomes(
    paths: list[list[dict[str, Any]]],
    *,
    entry: float,
    stop: float | None = None,
    target: float | None = None,
    side: str = "BUY",
) -> dict[str, Any]:
    """Compute P(up), expected return, and target/stop-first probabilities.

    Same-bar target+stop → stop first (matches timemachine.sim).
    """
    if not paths:
        return {
            "p_up": None,
            "expected_return_pct": None,
            "p_target_first": None,
            "p_stop_first": None,
            "p_neither": None,
            "pred_high": None,
            "pred_low": None,
            "pred_close_mean": None,
            "call": "NEUTRAL",
            "paths_n": 0,
        }

    side_u = (side or "BUY").upper()
    is_long = side_u in {"BUY", "LONG", "CALL"}
    last_closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    outcomes: list[int] = []  # 1 target, -1 stop, 0 neither

    for path in paths:
        if not path:
            continue
        closes = [float(b["close"]) for b in path]
        ph = max(float(b["high"]) for b in path)
        pl = min(float(b["low"]) for b in path)
        last_closes.append(closes[-1])
        highs.append(ph)
        lows.append(pl)

        if stop is None or target is None or entry <= 0:
            outcomes.append(0)
            continue

        done = 0
        for bar in path:
            h = float(bar["high"])
            l = float(bar["low"])
            if is_long:
                stop_hit = l <= stop
                tgt_hit = h >= target
            else:
                stop_hit = h >= stop
                tgt_hit = l <= target
            if stop_hit and tgt_hit:
                done = -1
                break
            if stop_hit:
                done = -1
                break
            if tgt_hit:
                done = 1
                break
        outcomes.append(done)

    n = len(last_closes) or 1
    p_up = sum(1 for c in last_closes if c > entry) / n if entry else None
    rets = [((c / entry) - 1.0) * 100.0 for c in last_closes] if entry else []
    expected_return_pct = sum(rets) / len(rets) if rets else None

    if stop is not None and target is not None and outcomes:
        p_tgt = sum(1 for o in outcomes if o == 1) / len(outcomes)
        p_stp = sum(1 for o in outcomes if o == -1) / len(outcomes)
        p_neither = 1.0 - p_tgt - p_stp
    else:
        p_tgt = p_stp = p_neither = None

    call = "NEUTRAL"
    if p_up is not None and p_tgt is not None and p_stp is not None:
        if p_up >= 0.55 and p_tgt >= p_stp + 0.05:
            call = "BUY" if is_long else "SELL"
        elif p_up <= 0.45 or p_stp >= p_tgt + 0.05:
            call = "AVOID"
    elif p_up is not None:
        if p_up >= 0.58:
            call = "BUY" if is_long else "SELL"
        elif p_up <= 0.42:
            call = "AVOID"

    return {
        "p_up": round(p_up, 4) if p_up is not None else None,
        "expected_return_pct": round(expected_return_pct, 4) if expected_return_pct is not None else None,
        "p_target_first": round(p_tgt, 4) if p_tgt is not None else None,
        "p_stop_first": round(p_stp, 4) if p_stp is not None else None,
        "p_neither": round(p_neither, 4) if p_neither is not None else None,
        "pred_high": round(sum(highs) / len(highs), 4) if highs else None,
        "pred_low": round(sum(lows) / len(lows), 4) if lows else None,
        "pred_close_mean": round(sum(last_closes) / len(last_closes), 4) if last_closes else None,
        "call": call,
        "paths_n": len(paths),
        "entry": entry,
        "stop": stop,
        "target": target,
        "side": side_u,
    }


def vote_from_stats(stats: dict[str, Any] | None) -> float | None:
    """Map Kronos stats to a consensus vote in [-1, +1]."""
    if not stats:
        return None
    votes: list[float] = []
    p_up = stats.get("p_up")
    if p_up is not None:
        votes.append(_clamp((float(p_up) - 0.5) / 0.15))
    pt = stats.get("p_target_first")
    ps = stats.get("p_stop_first")
    if pt is not None and ps is not None:
        votes.append(_clamp((float(pt) - float(ps)) / 0.3))
    if not votes:
        return None
    return round(sum(votes) / len(votes), 4)
