"""Unlock stats from shadow book."""

from __future__ import annotations

from typing import Any

import numpy as np

from config import trading_config as cfg


def _resolved_qs(kind: str, *, recommended_only: bool = True):
    from evidence.models import ShadowTrade

    qs = ShadowTrade.objects.filter(kind=kind).exclude(
        outcome__in=[ShadowTrade.Outcome.OPEN, ShadowTrade.Outcome.NO_DATA]
    )
    if recommended_only:
        qs = qs.filter(would_recommend=True)
    return qs.order_by("session_date", "id")


def _bootstrap_ci(values: list[float], *, n_boot: int = 1000, alpha: float = 0.10, seed: int = 19) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=float)
    means = []
    for _ in range(n_boot):
        sample = rng.choice(arr, size=len(arr), replace=True)
        means.append(float(sample.mean()))
    lo = float(np.percentile(means, 100 * (alpha / 2)))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return lo, hi


def kind_stats(kind: str) -> dict[str, Any]:
    qs = list(_resolved_qs(kind))
    nets = [float(r.net_pnl) for r in qs]
    n = len(nets)
    sessions = len({r.session_date for r in qs})
    wins = sum(1 for r in qs if float(r.net_pnl) > 0)
    avg = float(np.mean(nets)) if nets else 0.0
    total = float(np.sum(nets)) if nets else 0.0
    ci_lo, ci_hi = _bootstrap_ci(nets) if n >= 10 else (None, None)
    # Drawdown on cumulative net
    max_dd = 0.0
    if nets:
        eq = np.cumsum(nets)
        peak = np.maximum.accumulate(eq)
        max_dd = float(abs((eq - peak).min()))
    # Calibration by score/prob buckets
    cal = []
    scored = [(float(r.probability or r.score or 0), 1 if float(r.net_pnl) > 0 else 0) for r in qs]
    if scored:
        for lo, hi in ((0.0, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 1.01)):
            bucket = [w for p, w in scored if lo <= p < hi]
            if not bucket:
                continue
            cal.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "n": len(bucket),
                    "actual_win_rate": round(sum(bucket) / len(bucket), 3),
                }
            )
    return {
        "kind": kind,
        "n": n,
        "sessions": sessions,
        "win_rate": round(wins / n, 3) if n else None,
        "avg_net": round(avg, 2) if n else None,
        "total_net": round(total, 2) if n else None,
        "max_dd": round(max_dd, 2),
        "ci_lo": round(ci_lo, 2) if ci_lo is not None else None,
        "ci_hi": round(ci_hi, 2) if ci_hi is not None else None,
        "calibration": cal,
    }


def _unlock_from_stats(stats: dict, *, min_n: int, min_sessions: int) -> dict[str, Any]:
    unlocked = (
        stats["n"] >= min_n
        and stats["sessions"] >= min_sessions
        and stats.get("avg_net") is not None
        and float(stats["avg_net"]) > 0
        and stats.get("ci_lo") is not None
        and float(stats["ci_lo"]) > 0
    )
    reason = "unlocked" if unlocked else "practice only — need more positive measured results"
    if stats["n"] < min_n:
        reason = f"practice {stats['n']} of {min_n} recommended trades"
    elif stats["sessions"] < min_sessions:
        reason = f"practice {stats['sessions']} of {min_sessions} sessions"
    elif stats.get("avg_net") is not None and float(stats["avg_net"]) <= 0:
        reason = f"average net ₹{stats['avg_net']} ≤ 0 — keep practicing"
    elif stats.get("ci_lo") is not None and float(stats["ci_lo"]) <= 0:
        reason = f"90% interval lower bound ₹{stats['ci_lo']} ≤ 0 — not proven yet"
    return {
        "unlocked": unlocked,
        "n": stats["n"],
        "needed": min_n,
        "sessions": stats["sessions"],
        "sessions_needed": min_sessions,
        "avg_net": stats.get("avg_net"),
        "ci_lo": stats.get("ci_lo"),
        "ci_hi": stats.get("ci_hi"),
        "win_rate": stats.get("win_rate"),
        "reason": reason,
        "stats": stats,
    }


def fno_unlock_status() -> dict[str, Any]:
    return _unlock_from_stats(
        kind_stats("options"),
        min_n=int(getattr(cfg, "FNO_UNLOCK_MIN_TRADES", 40)),
        min_sessions=int(getattr(cfg, "FNO_UNLOCK_MIN_SESSIONS", 15)),
    )


def stocks_unlock_status() -> dict[str, Any]:
    st = kind_stats("stocks")
    # Until enough shadow trades exist, stocks stay unlocked for Ready cards
    if st["n"] < 10:
        return {
            "unlocked": True,
            "n": st["n"],
            "needed": int(getattr(cfg, "STOCKS_UNLOCK_MIN_TRADES", 60)),
            "sessions": st["sessions"],
            "sessions_needed": int(getattr(cfg, "STOCKS_UNLOCK_MIN_SESSIONS", 15)),
            "avg_net": st.get("avg_net"),
            "ci_lo": st.get("ci_lo"),
            "ci_hi": st.get("ci_hi"),
            "win_rate": st.get("win_rate"),
            "reason": "stocks unlocked pending shadow sample",
            "stats": st,
        }
    return _unlock_from_stats(
        st,
        min_n=int(getattr(cfg, "STOCKS_UNLOCK_MIN_TRADES", 60)),
        min_sessions=int(getattr(cfg, "STOCKS_UNLOCK_MIN_SESSIONS", 15)),
    )


def shadow_hit_rate(*, kind: str = "stocks", lookback: int = 20) -> dict[str, Any] | None:
    qs = list(_resolved_qs(kind)[:lookback])
    if len(qs) < lookback:
        return None
    hits = sum(1 for r in qs if r.outcome == "target" or float(r.net_pnl) > 0)
    return {"n": len(qs), "hit_rate": hits / len(qs)}


def measured_win_rate_for_score(score: float, *, kind: str = "options") -> float | None:
    """Replace hand-set F&O score with measured win rate in that bucket (when unlocked)."""
    if not fno_unlock_status().get("unlocked"):
        return None
    st = kind_stats(kind)
    for row in st.get("calibration") or []:
        if row["lo"] <= score < row["hi"] and row["n"] >= 8:
            return float(row["actual_win_rate"])
    return None


def vote_usefulness(lookback: int = 100) -> dict[str, Any]:
    """How often each model's vote sign matched eventual win (display / weight learning)."""
    from evidence.models import ShadowTrade

    qs = list(
        ShadowTrade.objects.exclude(outcome__in=["open", "no_data"])
        .filter(would_recommend=True)
        .order_by("-session_date")[:lookback]
    )
    tallies: dict[str, list[int]] = {}
    for r in qs:
        won = 1 if float(r.net_pnl) > 0 else 0
        votes = r.model_votes or {}
        for name, v in votes.items():
            if v is None:
                continue
            try:
                agree = 1 if (float(v) > 0 and won) or (float(v) < 0 and not won) else 0
            except (TypeError, ValueError):
                continue
            tallies.setdefault(name, []).append(agree)
    return {k: {"n": len(v), "agree_rate": round(sum(v) / len(v), 3)} for k, v in tallies.items() if v}
