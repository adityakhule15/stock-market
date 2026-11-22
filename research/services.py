"""Research run orchestration — fail-open to StockScope signals."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from research.adapter import (
    agents_enabled,
    build_portfolio_context,
    build_ta_config,
    invoke_runner,
    load_agents_settings,
    provider_keys_present,
    to_yahoo_ticker,
    today_ist,
)
from research.models import ResearchRun
from research.veto import apply_agent_veto, normalize_rating

logger = logging.getLogger(__name__)


def _agents_unavailable_payload(reason: str) -> dict[str, Any]:
    return {
        "rating": "",
        "bucket": "fail_open",
        "badge": "agents_unavailable",
        "unavailable": True,
        "vetoed": False,
        "neutral": False,
        "rank_delta": 0.0,
        "summary": {},
        "report_url": None,
        "reason": reason,
    }


def daily_call_count(day: date | None = None) -> int:
    day = day or today_ist()
    return ResearchRun.objects.filter(
        as_of=day,
        status__in=[ResearchRun.Status.OK, ResearchRun.Status.RUNNING, ResearchRun.Status.QUEUED],
    ).count()


def under_daily_caps() -> tuple[bool, str]:
    agents = load_agents_settings()
    used = daily_call_count()
    cap = int(agents.get("daily_call_cap") or 0)
    if cap and used >= cap:
        return False, f"daily call cap reached ({used}/{cap})"
    # cost cap reserved for when token cost is available
    return True, ""


def run_analysis(
    symbol: str,
    as_of: date | str | None = None,
    signal=None,
    *,
    analysts: list[str] | None = None,
    fno_underlying: bool = False,
    portfolio: dict[str, Any] | None = ...,  # type: ignore[assignment]
    sync: bool = True,
) -> ResearchRun:
    """Run or enqueue a TradingAgents analysis and persist ResearchRun.

    portfolio=... → build from wallet; portfolio=None → omit; dict → use as-is.
    """
    if isinstance(as_of, str):
        as_of_d = date.fromisoformat(as_of)
    elif as_of is None:
        as_of_d = today_ist()
    else:
        as_of_d = as_of

    try:
        yahoo = to_yahoo_ticker(symbol, fno_underlying=fno_underlying)
    except ValueError as exc:
        run = ResearchRun.objects.create(
            symbol=symbol,
            yahoo_ticker=symbol,
            as_of=as_of_d,
            status=ResearchRun.Status.ERROR,
            error=str(exc),
            signal=signal,
        )
        return run

    agents = load_agents_settings()
    selected = analysts or list(agents.get("selected_analysts") or [])
    ta_config = build_ta_config()
    # Explicit India benchmark for indices / .NS
    if yahoo.startswith("^") or yahoo.endswith(".NS") or yahoo.endswith(".BO"):
        ta_config.setdefault("benchmark_ticker", "^NSEI" if not yahoo.startswith("^NSEBANK") else "^NSEBANK")
        if yahoo in {"^NSEI", "^INDIAVIX"} or yahoo.endswith(".NS"):
            ta_config["benchmark_ticker"] = "^NSEI"

    if portfolio is ...:
        book = build_portfolio_context(known=True)
    else:
        book = portfolio

    run = ResearchRun.objects.create(
        symbol=symbol,
        yahoo_ticker=yahoo,
        as_of=as_of_d,
        status=ResearchRun.Status.QUEUED,
        config_snapshot={
            "ta_config": ta_config,
            "analysts": selected,
            "agents_settings": {
                k: agents[k]
                for k in (
                    "top_n",
                    "timeout_sec",
                    "max_debate_rounds",
                    "max_risk_rounds",
                    "llm_provider",
                    "checkpoint_enabled",
                )
                if k in agents
            },
            "fno_underlying": fno_underlying,
        },
        signal=signal,
    )

    if not sync:
        from research.workers import enqueue_research_run

        enqueue_research_run(run.id)
        return run

    return execute_research_run(run.id, portfolio=book)


def execute_research_run(
    run_id: int, *, portfolio: dict[str, Any] | None = ...
) -> ResearchRun:  # type: ignore[assignment]
    with transaction.atomic():
        run = ResearchRun.objects.select_for_update().get(pk=run_id)
        run.status = ResearchRun.Status.RUNNING
        run.started_at = timezone.now()
        run.error = ""
        run.save(update_fields=["status", "started_at", "error", "updated_at"])

    agents = load_agents_settings()
    snap = run.config_snapshot or {}
    ta_config = snap.get("ta_config") or build_ta_config()
    selected = snap.get("analysts") or agents.get("selected_analysts")

    if portfolio is ...:
        book = build_portfolio_context(known=True)
    else:
        book = portfolio

    # Fail-open early if no keys / disabled
    if not agents_enabled():
        return _finish_error(run, "agents disabled", timeout=False)
    if not provider_keys_present() and (agents.get("llm_provider") not in {"ollama", "chatgpt"}):
        return _finish_error(run, "LLM provider key missing", timeout=False)

    retries = int(agents.get("retry_budget") or 0)
    last: dict[str, Any] = {}
    for attempt in range(retries + 1):
        req = {
            "action": "analyze",
            "ticker": run.yahoo_ticker,
            "date": run.as_of.isoformat(),
            "selected_analysts": selected,
            "portfolio": book,
            "config": ta_config,
            "checkpoint_enabled": bool(agents.get("checkpoint_enabled", True)),
            "save_html": True,
            "benchmark_ticker": ta_config.get("benchmark_ticker"),
        }
        last = invoke_runner(req, timeout_sec=int(agents.get("timeout_sec") or 600))
        if last.get("ok"):
            break
        if last.get("timeout"):
            return _finish_error(run, last.get("error") or "timeout", timeout=True)
        logger.warning(
            "Research run %s attempt %s failed: %s", run.id, attempt + 1, last.get("error")
        )

    if not last.get("ok"):
        # .BO fallback once if .NS failed hard
        if run.yahoo_ticker.endswith(".NS"):
            alt = run.yahoo_ticker[:-3] + ".BO"
            req["ticker"] = alt
            last = invoke_runner(req, timeout_sec=int(agents.get("timeout_sec") or 600))
            if last.get("ok"):
                run.yahoo_ticker = alt
        if not last.get("ok"):
            return _finish_error(run, last.get("error") or "runner failed", timeout=bool(last.get("timeout")))

    rating = normalize_rating(last.get("rating") or last.get("decision") or "")
    summary = last.get("summary") or {}
    run.status = ResearchRun.Status.OK
    run.rating = rating
    run.decision = str(last.get("decision") or rating)
    run.report_json = {
        "summary": summary,
        "analysts": last.get("analysts") or selected,
        "raw_keys": [k for k in last.keys() if k not in {"config_snapshot", "summary"}],
    }
    run.report_html_path = str(last.get("report_html_path") or "")
    run.report_dir = str(last.get("report_dir") or "")
    if last.get("config_snapshot"):
        snap["runner_config"] = last["config_snapshot"]
        run.config_snapshot = snap
    run.finished_at = timezone.now()
    run.error = ""
    run.save()

    if run.signal_id:
        _attach_to_signal(run)

    return run


def _finish_error(run: ResearchRun, message: str, *, timeout: bool) -> ResearchRun:
    run.status = ResearchRun.Status.TIMEOUT if timeout else ResearchRun.Status.ERROR
    run.error = message
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])
    if run.signal_id:
        _attach_unavailable(run, message)
    return run


def _attach_unavailable(run: ResearchRun, reason: str) -> None:
    from signals.models import Signal

    try:
        sig = Signal.objects.get(pk=run.signal_id)
    except Signal.DoesNotExist:
        return
    pipe = dict(sig.pipeline or {})
    pipe["agents"] = {
        **_agents_unavailable_payload(reason),
        "research_run_id": run.id,
    }
    sig.pipeline = pipe
    sig.save(update_fields=["pipeline"])


def _attach_to_signal(run: ResearchRun) -> None:
    from signals.models import Signal

    try:
        sig = Signal.objects.get(pk=run.signal_id)
    except Signal.DoesNotExist:
        return

    gate = dict(sig.gate or {})
    passed = gate.get("result") == "PASS" or bool(gate.get("ok"))
    reasons = list(gate.get("blocked_by") or gate.get("reasons") or [])
    veto = apply_agent_veto(
        gate_passed=bool(passed),
        gate_reasons=reasons,
        rating=run.rating,
    )
    summary = (run.report_json or {}).get("summary") or {}
    agents_meta = {
        **veto["agents"],
        "research_run_id": run.id,
        "report_html_path": run.report_html_path,
        "report_dir": run.report_dir,
        "bull_case": _pick_text(summary, "investment_debate_state", "bull_history"),
        "bear_case": _pick_text(summary, "investment_debate_state", "bear_history"),
        "key_risks": _pick_text(summary, "risk_debate_state", "history")
        or _pick_text(summary, "final_trade_decision"),
        "final_rating": run.rating,
        "decision": run.decision,
        "summary": _pick_text(summary, "final_trade_decision")
        or _pick_text(summary, "investment_plan"),
        "fno_underlying_only": bool((run.config_snapshot or {}).get("fno_underlying")),
        "indian_data_note": (
            "SEC EDGAR as-filed does not cover pure NSE names; Yahoo fundamentals "
            "may be withheld for past dates; F&O uses underlying only."
        ),
    }
    pipe = dict(sig.pipeline or {})
    pipe["agents"] = agents_meta

    # Apply veto to gate + status — never unblock
    new_passed = bool(veto["passed"]) and bool(passed)
    if not veto["passed"] and passed:
        gate["result"] = "BLOCK"
        gate["ok"] = False
        gate["blocked_by"] = veto["reasons"]
        sig.status = Signal.Status.SKIPPED
        sig.skip_reason = "agents_veto"
    gate["agents"] = agents_meta
    sig.gate = gate
    sig.pipeline = pipe
    # Preserve NEW only if still passed
    if new_passed and sig.status == Signal.Status.SKIPPED and "agents_veto" not in (
        sig.skip_reason or ""
    ):
        pass  # do not resurrect unrelated skips
    sig.save(update_fields=["gate", "pipeline", "status", "skip_reason"])


def _pick_text(summary: dict, *keys: str) -> str:
    cur: Any = summary
    for k in keys:
        if not isinstance(cur, dict):
            return str(cur)[:1500] if cur else ""
        cur = cur.get(k)
    if cur is None:
        return ""
    return str(cur)[:1500]


def review_pass_signals(signals: list, *, top_n: int | None = None) -> list:
    """After LightGBM+gate PASS: run agents on top-N, mutate signals in place.

    Fail-open: missing key / disabled / errors leave signal as today + badge.
    Never promotes a blocked signal.
    """
    agents = load_agents_settings()
    if not agents_enabled():
        for s in signals:
            _stamp_unavailable(s, "agents disabled")
        return signals

    if not provider_keys_present() and agents.get("llm_provider") not in {"ollama", "chatgpt"}:
        for s in signals:
            _stamp_unavailable(s, "LLM key missing")
        return signals

    n = int(top_n if top_n is not None else agents.get("top_n") or 3)
    # Prefer already-PASS signals by probability
    pass_list = []
    for s in signals:
        gate = s.gate or {}
        if gate.get("result") == "PASS" or gate.get("ok"):
            pass_list.append(s)
    pass_list.sort(key=lambda s: float(getattr(s, "probability", 0) or 0), reverse=True)

    reviewed = 0
    for s in pass_list:
        if reviewed >= n:
            break
        ok_cap, reason = under_daily_caps()
        if not ok_cap:
            _stamp_unavailable(s, reason)
            continue
        is_fno = getattr(s, "strategy", "") == "fno_intraday"
        symbol = s.instrument.symbol
        try:
            run = run_analysis(
                symbol,
                as_of=today_ist(),
                signal=s,
                fno_underlying=is_fno,
                sync=True,
            )
            # reload signal fields from DB
            s.refresh_from_db()
            reviewed += 1
            if run.status != ResearchRun.Status.OK:
                _stamp_unavailable(s, run.error or "agents error")
        except Exception as exc:  # noqa: BLE001
            logger.exception("agents review failed for %s", symbol)
            _stamp_unavailable(s, str(exc))
    return signals


def _stamp_unavailable(signal, reason: str) -> None:
    pipe = dict(getattr(signal, "pipeline", None) or {})
    pipe["agents"] = {
        **_agents_unavailable_payload(reason),
    }
    signal.pipeline = pipe
    gate = dict(getattr(signal, "gate", None) or {})
    gate["agents"] = pipe["agents"]
    signal.gate = gate
    try:
        signal.save(update_fields=["pipeline", "gate"])
    except Exception:  # noqa: BLE001
        pass


def attach_underlying_research(signals: list) -> list:
    """For F&O tickets: run agents on the underlying; do not change strike/lot/gate capital rules.

    Attaches pipeline.agents with fno_underlying_only=True. Does not unblock watches.
    """
    agents = load_agents_settings()
    if not agents_enabled():
        return signals
    if not provider_keys_present() and agents.get("llm_provider") not in {"ollama", "chatgpt"}:
        for s in signals:
            _stamp_unavailable(s, "LLM key missing")
        return signals

    seen: set[str] = set()
    n = int(agents.get("top_n") or 3)
    count = 0
    for s in signals:
        if count >= n:
            break
        reasons = getattr(s, "reasons", None) or {}
        fno = reasons.get("fno") if isinstance(reasons, dict) else {}
        underlying = ""
        if isinstance(fno, dict):
            underlying = str(fno.get("underlying") or "")
        if not underlying:
            underlying = s.instrument.symbol
        key = underlying.upper()
        if key in seen:
            # copy prior agents meta if any sibling already ran
            continue
        seen.add(key)
        ok_cap, reason = under_daily_caps()
        if not ok_cap:
            _stamp_unavailable(s, reason)
            continue
        try:
            # Do not link Signal during run — F&O gate is already watch/BLOCK; avoid veto side-effects.
            run = run_analysis(
                underlying,
                as_of=today_ist(),
                signal=None,
                fno_underlying=True,
                sync=True,
            )
            pipe = dict(s.pipeline or {})
            if run.status != ResearchRun.Status.OK:
                _stamp_unavailable(s, run.error or "agents error")
                count += 1
                continue
            from research.veto import rating_bucket

            bucket = rating_bucket(run.rating)
            summary = (run.report_json or {}).get("summary") or {}
            agents_meta = {
                "rating": run.rating,
                "bucket": bucket,
                "badge": {
                    "buy": "agents_bullish",
                    "hold": "agents_neutral",
                    "sell": "agents_bearish_context",
                    "fail_open": "agents_unavailable",
                }.get(bucket, "agents_unavailable"),
                "vetoed": False,
                "neutral": bucket == "hold",
                "unavailable": bucket == "fail_open",
                "rank_delta": 0.0,
                "research_run_id": run.id,
                "report_html_path": run.report_html_path,
                "report_dir": run.report_dir,
                "final_rating": run.rating,
                "summary": _pick_text(summary, "final_trade_decision"),
                "bull_case": _pick_text(summary, "investment_debate_state", "bull_history"),
                "bear_case": _pick_text(summary, "investment_debate_state", "bear_history"),
                "key_risks": _pick_text(summary, "risk_debate_state", "history"),
                "fno_underlying_only": True,
                "underlying": underlying,
                "limitation": (
                    "TradingAgents analyzes the underlying for direction/risk only; "
                    "strike, lot size, and Groww charges stay in StockScope."
                ),
                "indian_data_note": (
                    "SEC EDGAR as-filed does not cover pure NSE names; Yahoo fundamentals "
                    "may be withheld for past dates."
                ),
            }
            pipe["agents"] = agents_meta
            s.pipeline = pipe
            s.save(update_fields=["pipeline"])
            run.signal = s
            run.save(update_fields=["signal", "updated_at"])
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("underlying research failed for %s", underlying)
            _stamp_unavailable(s, str(exc))
    return signals


def settle_memory(*, wait: bool = True) -> dict[str, Any]:
    agents = load_agents_settings()
    ta_config = build_ta_config()
    result = invoke_runner(
        {"action": "settle", "wait": wait, "config": ta_config},
        timeout_sec=max(120, int(agents.get("timeout_sec") or 600)),
    )
    return result


def parse_memory_entries(limit: int = 50) -> list[dict[str, Any]]:
    """Best-effort parse of trading_memory.md for Journal UI."""
    from research.adapter import ensure_data_dirs

    path = ensure_data_dirs()["memory_log_path"]
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    blocks = text.split("\n## ")
    for block in blocks[1:] if len(blocks) > 1 else []:
        lines = block.strip().splitlines()
        if not lines:
            continue
        header = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        entry: dict[str, Any] = {"header": header, "body": body[:3000]}
        # Heuristic fields
        for line in lines[1:40]:
            low = line.lower()
            if "return" in low and ":" in line:
                entry.setdefault("return", line.split(":", 1)[-1].strip())
            if "alpha" in low and ":" in line:
                entry.setdefault("alpha", line.split(":", 1)[-1].strip())
            if "reflect" in low:
                entry.setdefault("reflection", body)
            if "rating" in low and ":" in line:
                entry.setdefault("rating", line.split(":", 1)[-1].strip())
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries
