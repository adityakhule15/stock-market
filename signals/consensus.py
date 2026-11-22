"""Multi-model consensus in filter mode.

LightGBM + risk_gate still decide PASS/BLOCK. Consensus can only:
- veto a PASS when score is strongly negative and enough models agree
- re-rank via score
Never turns BLOCK into PASS. Never overrides capital rules.
"""

from __future__ import annotations

from typing import Any

from config import trading_config as cfg
from forecast.stats import vote_from_stats
from research.veto import rating_bucket


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def vote_lightgbm(probability: float | None, *, min_prob: float | None = None) -> float | None:
    if probability is None:
        return None
    return _clamp((float(probability) - 0.5) / 0.15)


def vote_meta(meta_p: float | None) -> float | None:
    if meta_p is None:
        return None
    return _clamp((float(meta_p) - 0.5) / 0.15)


def vote_time_machine(tm: dict[str, Any] | None) -> float | None:
    if not tm:
        return None
    pt = tm.get("p_target")
    ps = tm.get("p_stop")
    if pt is None or ps is None:
        # Fall back to verdict
        v = str(tm.get("verdict") or "").upper()
        if v == "EDGE":
            return 0.6
        if v == "COIN_FLIP":
            return -0.8
        if v in {"NEUTRAL", "WEAK"}:
            return 0.0
        return None
    return _clamp((float(pt) - float(ps)) / 0.3)


def vote_tradingagents(rating: str | None) -> float | None:
    bucket = rating_bucket(rating)
    if bucket == "fail_open":
        return None
    mapping = {"buy": 1.0, "hold": 0.0, "sell": -1.0}
    # Distinguish Overweight / Underweight via raw rating
    raw = (rating or "").strip().lower()
    if raw == "overweight":
        return 0.5
    if raw == "underweight":
        return -0.5
    return mapping.get(bucket)


def vote_insider(insider: dict[str, Any] | None) -> float | None:
    if not insider or insider.get("unavailable"):
        return None
    if insider.get("vote") is not None:
        return float(insider["vote"])
    flag = insider.get("flag")
    if flag == "buy":
        return 0.5
    if flag == "sell":
        return -0.5
    if flag == "pledge":
        return -1.0
    return None  # none / no activity → exclude


def collect_model_calls(signal) -> dict[str, Any]:
    """Build the per-model calls panel payload from a Signal."""
    pipe = getattr(signal, "pipeline", None) or {}
    tm = getattr(signal, "time_machine", None) or pipe.get("time_machine") or {}
    agents = pipe.get("agents") or {}
    kronos = pipe.get("kronos") or {}
    insider = pipe.get("insider") or {}
    analyst = pipe.get("analyst") or {}
    meta_p = None
    try:
        meta_p = (pipe.get("gate") or {}).get("meta_win_prob")
        if meta_p is None:
            meta_p = getattr(signal, "reasons", None) or {}
            if isinstance(meta_p, dict):
                meta_p = meta_p.get("meta_win_prob")
    except Exception:  # noqa: BLE001
        meta_p = None

    # Also check order-like fields stamped on pipeline
    if meta_p is None and isinstance(pipe.get("budget"), dict):
        meta_p = pipe["budget"].get("meta_win_prob")

    lightgbm = {
        "name": "LightGBM",
        "call": signal.side,
        "probability": float(signal.probability),
        "min_prob": None,
        "entry": float(signal.entry_trigger),
        "stop": float(signal.sl_trigger),
        "target": float(signal.target1),
        "rr_net": float(signal.rr_net),
        "product": signal.product,
        "strategy": signal.strategy,
        "parameters": {
            "probability": float(signal.probability),
            "rr_net": float(signal.rr_net),
            "qty": int(signal.qty),
            "max_loss": float(signal.max_loss),
        },
        "vote": vote_lightgbm(float(signal.probability)),
    }
    meta = {
        "name": "Meta filter",
        "call": None if meta_p is None else ("PASS" if float(meta_p) >= 0.42 else "BLOCK"),
        "p_win": meta_p,
        "parameters": {"p_win": meta_p},
        "vote": vote_meta(float(meta_p) if meta_p is not None else None),
        "unavailable": meta_p is None,
    }
    tm_call = {
        "name": "Time Machine",
        "call": tm.get("verdict"),
        "p_target": tm.get("p_target"),
        "p_stop": tm.get("p_stop"),
        "n": tm.get("n") or tm.get("sims"),
        "seed": tm.get("seed", getattr(cfg, "TM_SEED", 19)),
        "parameters": {
            "verdict": tm.get("verdict"),
            "p_target": tm.get("p_target"),
            "p_stop": tm.get("p_stop"),
            "n": tm.get("n") or 10_000,
            "seed": getattr(cfg, "TM_SEED", 19),
        },
        "vote": vote_time_machine(tm if isinstance(tm, dict) else None),
        "unavailable": not bool(tm),
    }
    k_stats = kronos.get("stats") or {}
    kronos_call = {
        "name": "Kronos",
        "call": kronos.get("call") or k_stats.get("call"),
        "stats": k_stats,
        "parameters": kronos.get("parameters") or {
            "model_id": kronos.get("model_id"),
            "lookback": kronos.get("lookback"),
            "paths": kronos.get("paths_n"),
            "pred_len": kronos.get("pred_len"),
        },
        "vote": vote_from_stats(k_stats) if kronos.get("ok") else None,
        "unavailable": not kronos.get("ok", False),
        "badge": kronos.get("badge"),
    }
    ta_rating = agents.get("rating") or agents.get("final_rating")
    ta_call = {
        "name": "TradingAgents",
        "call": ta_rating,
        "badge": agents.get("badge"),
        "summary": agents.get("summary"),
        "report_url": agents.get("report_url"),
        "parameters": {
            "rating": ta_rating,
            "badge": agents.get("badge"),
        },
        "vote": vote_tradingagents(ta_rating),
        "unavailable": bool(agents.get("unavailable")) or not ta_rating,
    }
    insider_call = {
        "name": "India insider",
        "call": insider.get("label") or insider.get("flag"),
        "net_value_inr": insider.get("net_value_inr"),
        "pledge_events": insider.get("pledge_events"),
        "parameters": {
            "flag": insider.get("flag"),
            "net_value_inr": insider.get("net_value_inr"),
            "lookback_days": insider.get("lookback_days", 30),
        },
        "vote": vote_insider(insider if isinstance(insider, dict) else None),
        "unavailable": bool(insider.get("unavailable") or insider.get("pending")),
    }
    analyst_call = {
        "name": "Analyst",
        "call": "facts_only",
        "summary": analyst.get("summary"),
        "results_inside_trade": analyst.get("results_inside_trade"),
        "parameters": {
            "revenue_yoy": (analyst.get("revenue") or {}).get("yoy"),
            "profit_yoy": (analyst.get("profit") or {}).get("yoy"),
            "results_date": analyst.get("results_date"),
        },
        "vote": None,  # never votes
        "unavailable": bool(analyst.get("unavailable") or analyst.get("pending")),
        "facts_only": True,
    }
    checklist = pipe.get("skill_checklist") or []

    return {
        "lightgbm": lightgbm,
        "meta": meta,
        "time_machine": tm_call,
        "kronos": kronos_call,
        "tradingagents": ta_call,
        "insider": insider_call,
        "analyst": analyst_call,
        "skill_checklist": checklist,
    }


def combine_votes(
    votes: dict[str, float | None],
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Weighted consensus score; renormalize over available models."""
    weights = dict(weights or getattr(cfg, "CONSENSUS_WEIGHTS", {}))
    available = {k: float(v) for k, v in votes.items() if v is not None and k in weights}
    if not available:
        return {
            "score": None,
            "n_models": 0,
            "agree_k": 0,
            "weights_used": {},
            "thin": True,
            "votes": votes,
        }
    wsum = sum(float(weights[k]) for k in available) or 1.0
    used = {k: float(weights[k]) / wsum for k in available}
    score = sum(used[k] * available[k] for k in available)
    agree_k = sum(1 for v in available.values() if v > 0)
    return {
        "score": round(score, 4),
        "n_models": len(available),
        "agree_k": agree_k,
        "weights_used": {k: round(v, 4) for k, v in used.items()},
        "thin": len(available) < int(getattr(cfg, "CONSENSUS_MIN_MODELS", 3)),
        "votes": {k: (round(v, 4) if v is not None else None) for k, v in votes.items()},
    }


def apply_consensus_filter(
    *,
    gate_passed: bool,
    gate_reasons: list[str] | None,
    consensus: dict[str, Any],
) -> dict[str, Any]:
    """Filter-mode application. Never unblocks a BLOCK."""
    reasons = list(gate_reasons or [])
    score = consensus.get("score")
    thin = bool(consensus.get("thin"))
    n = int(consensus.get("n_models") or 0)
    agree_k = int(consensus.get("agree_k") or 0)
    veto_thr = float(getattr(cfg, "CONSENSUS_VETO_THRESHOLD", -0.25))
    agree_thr = float(getattr(cfg, "CONSENSUS_AGREE_THRESHOLD", 0.15))
    veto_on = bool(getattr(cfg, "CONSENSUS_VETO_ENABLED", True))
    try:
        from portfolio.models import PortfolioSettings

        stored = (PortfolioSettings.objects.first() or None)
        mc = getattr(stored, "models_config", None) or {} if stored else {}
        if isinstance(mc, dict) and "consensus_veto" in mc:
            veto_on = bool(mc.get("consensus_veto"))
        if isinstance(mc, dict) and mc.get("consensus_enabled") is False:
            # Show-only: never veto via consensus
            veto_on = False
    except Exception:  # noqa: BLE001
        pass

    meta = {
        "score": score,
        "n_models": n,
        "agree_k": agree_k,
        "thin": thin,
        "badge": None,
        "vetoed": False,
        "rank_delta": 0.0,
        "final_call": None,
        "reason": "",
    }

    if not gate_passed:
        meta["badge"] = "consensus_skipped_blocked"
        meta["final_call"] = "BLOCK"
        meta["reason"] = "risk gate already blocked"
        return {
            "passed": False,
            "reasons": reasons,
            "consensus": meta,
            "status": "skipped_already_blocked",
        }

    if score is None:
        meta["badge"] = "consensus_unavailable"
        meta["final_call"] = "PASS"
        meta["reason"] = "no model votes"
        return {"passed": True, "reasons": reasons, "consensus": meta, "status": "unavailable"}

    # Rank nudge always available
    points = float(getattr(cfg, "CONSENSUS_RANK_POINTS", 10.0))
    meta["rank_delta"] = round(points * float(score), 3)

    if veto_on and (not thin) and float(score) <= veto_thr:
        if "consensus_veto" not in reasons:
            reasons = ["consensus_veto"] + reasons
        meta["badge"] = "consensus_veto"
        meta["vetoed"] = True
        meta["final_call"] = "BLOCK"
        meta["reason"] = f"consensus {score:.2f} ≤ {veto_thr} with {n} models"
        meta["rank_delta"] = -20.0
        return {"passed": False, "reasons": reasons, "consensus": meta, "status": "veto"}

    if float(score) >= agree_thr:
        meta["badge"] = f"models_agree_{agree_k}/{n}"
        meta["final_call"] = "PASS"
        meta["reason"] = f"models agree {agree_k}/{n} (score {score:.2f})"
        return {"passed": True, "reasons": reasons, "consensus": meta, "status": "agree"}

    meta["badge"] = "models_mixed"
    meta["final_call"] = "PASS"
    meta["reason"] = f"models mixed (score {score:.2f})"
    return {"passed": True, "reasons": reasons, "consensus": meta, "status": "mixed"}


def consensus_from_signal(signal) -> dict[str, Any]:
    calls = collect_model_calls(signal)
    votes = {
        "lightgbm": calls["lightgbm"]["vote"],
        "meta": calls["meta"]["vote"],
        "time_machine": calls["time_machine"]["vote"],
        "kronos": calls["kronos"]["vote"],
        "tradingagents": calls["tradingagents"]["vote"],
        "insider": calls["insider"]["vote"],
    }
    combined = combine_votes(votes)
    return {"models": calls, "consensus": combined}
