"""Background multi-model review after LightGBM + risk_gate.

Fills analyst, insider, Kronos, TradingAgents (top-N PASS), then consensus.
Never blocks the web request. Fail-open throughout.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from django.utils import timezone

from config import trading_config as cfg
from signals.consensus import apply_consensus_filter, consensus_from_signal
from signals.models import Signal
from signals.skill_checklist import build_skill_checklist

logger = logging.getLogger(__name__)


def _gate_passed(sig: Signal) -> bool:
    g = sig.gate or {}
    if isinstance(g, dict):
        if g.get("result") == "PASS" or g.get("ok") is True:
            return True
        if g.get("result") == "BLOCK" or g.get("ok") is False:
            return False
    return sig.status == Signal.Status.NEW


def review_signal(signal: Signal, *, run_agents: bool = False) -> dict[str, Any]:
    """Enrich one signal with analyst/insider/kronos (+ optional agents) and consensus."""
    pipe = dict(signal.pipeline or {})
    sym = signal.instrument.symbol

    # Analyst (facts only)
    try:
        from signals.analyst import analyst_for_symbol

        hold = int(getattr(cfg, "AGENTS_HOLDING_PERIOD_DAYS", 5) or 5)
        pipe["analyst"] = analyst_for_symbol(sym, hold_days=hold)
    except Exception as exc:  # noqa: BLE001
        logger.debug("analyst failed: %s", exc)
        pipe["analyst"] = {"ok": False, "unavailable": True, "vote": None, "reason": str(exc)[:160]}

    # India insider
    try:
        from signals.insider import insider_for_symbol

        pipe["insider"] = insider_for_symbol(sym)
    except Exception as exc:  # noqa: BLE001
        logger.debug("insider failed: %s", exc)
        pipe["insider"] = {
            "ok": False,
            "unavailable": True,
            "flag": "none",
            "vote": None,
            "reason": str(exc)[:160],
        }

    # Kronos
    try:
        from forecast.adapter import kronos_enabled
        from forecast.services import attach_kronos_to_signal

        if kronos_enabled():
            # attach writes pipeline['kronos'] and saves — reload after
            signal.pipeline = pipe
            signal.save(update_fields=["pipeline"])
            attach_kronos_to_signal(signal)
            signal.refresh_from_db()
            pipe = dict(signal.pipeline or {})
        else:
            pipe["kronos"] = {
                "ok": False,
                "badge": "kronos_unavailable",
                "call": "NEUTRAL",
                "reason": "kronos disabled",
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("kronos attach failed: %s", exc)
        pipe["kronos"] = {
            "ok": False,
            "badge": "kronos_unavailable",
            "call": "NEUTRAL",
            "reason": str(exc)[:160],
        }

    # TradingAgents (optional, expensive)
    if run_agents:
        try:
            from research.adapter import agents_enabled
            from research.services import run_analysis

            if agents_enabled() and _gate_passed(signal):
                run_analysis(sym, signal=signal, sync=True)
                signal.refresh_from_db()
                pipe = dict(signal.pipeline or {})
                # merge stages we already computed
                pipe.setdefault("analyst", pipe.get("analyst"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("agents in model_review failed")
            pipe.setdefault(
                "agents",
                {"badge": "agents_unavailable", "unavailable": True, "reason": str(exc)[:160]},
            )

    pipe["skill_checklist"] = build_skill_checklist(pipe, gate_passed=_gate_passed(signal))
    signal.pipeline = pipe
    signal.save(update_fields=["pipeline"])

    # Consensus
    bundle = consensus_from_signal(signal)
    gate = dict(signal.gate or {})
    reasons = list(gate.get("blocked_by") or gate.get("reasons") or [])
    applied = apply_consensus_filter(
        gate_passed=_gate_passed(signal),
        gate_reasons=reasons,
        consensus=bundle["consensus"],
    )
    meta = applied["consensus"]
    pipe["model_calls"] = bundle["models"]
    pipe["consensus"] = {
        **bundle["consensus"],
        **meta,
        "models": {k: {"call": v.get("call"), "vote": v.get("vote"), "parameters": v.get("parameters")} for k, v in bundle["models"].items() if k != "skill_checklist"},
        "skill_checklist": bundle["models"].get("skill_checklist"),
        "updated_at": timezone.now().isoformat(),
    }
    signal.pipeline = pipe

    if applied["status"] == "veto" and _gate_passed(signal):
        gate["result"] = "BLOCK"
        gate["ok"] = False
        gate["blocked_by"] = applied["reasons"]
        gate["agents"] = gate.get("agents")  # keep
        signal.gate = gate
        signal.status = Signal.Status.SKIPPED
        signal.skip_reason = "consensus_veto"
        signal.save(update_fields=["pipeline", "gate", "status", "skip_reason"])
    else:
        # Stamp consensus into gate for UI without changing PASS
        gate["consensus"] = meta
        signal.gate = gate
        signal.save(update_fields=["pipeline", "gate"])

    return pipe.get("consensus") or {}


def review_signals(
    signals: Iterable[Signal],
    *,
    agents_top_n: int | None = None,
) -> list[dict[str, Any]]:
    """Review a batch. TradingAgents only on top-N PASS by probability."""
    sigs = list(signals)
    top_n = int(
        agents_top_n
        if agents_top_n is not None
        else getattr(cfg, "AGENTS_TOP_N", 3)
    )
    passed = [s for s in sigs if _gate_passed(s)]
    passed_sorted = sorted(passed, key=lambda s: float(s.probability or 0), reverse=True)
    agents_ids = {s.id for s in passed_sorted[: max(0, top_n)]}

    out: list[dict[str, Any]] = []
    for s in sigs:
        try:
            out.append(review_signal(s, run_agents=s.id in agents_ids))
        except Exception:  # noqa: BLE001
            logger.exception("model review failed for signal %s", getattr(s, "id", None))
    try:
        _telegram_summary(sigs, out)
    except Exception:  # noqa: BLE001
        logger.debug("telegram consensus summary skipped", exc_info=True)
    return out


def _telegram_summary(sigs: list[Signal], consensuses: list[dict[str, Any]]) -> None:
    from notifications.telegram import send_message

    lines = ["StockScope combined calls"]
    for s, cs in zip(sigs, consensuses):
        if not isinstance(cs, dict):
            continue
        call = cs.get("final_call") or cs.get("badge") or "—"
        lines.append(
            f"{s.instrument.symbol} {s.side} gate={(s.gate or {}).get('result')} "
            f"→ {call} score={cs.get('score')}"
        )
    if len(lines) > 1:
        send_message("\n".join(lines[:20]))


def enqueue_model_review(signal_ids: list[int]) -> None:
    from research.workers import enqueue_callable

    ids = [int(i) for i in signal_ids if i]

    def _job():
        qs = list(Signal.objects.filter(id__in=ids).select_related("instrument"))
        if qs:
            review_signals(qs)

    if ids:
        enqueue_callable("model-review", _job)
