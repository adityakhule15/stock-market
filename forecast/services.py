"""Kronos forecast orchestration — cache + fail-open."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.utils import timezone

from forecast.adapter import (
    bars_to_payload,
    invoke_kronos,
    kronos_enabled,
    kronos_sidecar_installed,
    load_kronos_settings,
    next_session_timestamps,
    today_ist,
)
from forecast.models import KronosForecast
from forecast.stats import path_outcomes

logger = logging.getLogger(__name__)


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "badge": "kronos_unavailable",
        "call": "NEUTRAL",
        "stats": {},
        "reason": reason,
    }


def get_cached(
    symbol: str,
    as_of: date | None = None,
    *,
    interval: str = "1d",
    max_age_hours: float = 24.0,
) -> KronosForecast | None:
    as_of = as_of or today_ist()
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    row = (
        KronosForecast.objects.filter(
            as_of=as_of,
            interval=interval,
            status=KronosForecast.Status.OK,
            symbol__in={bare, f"{bare}.NS", symbol.upper()},
        )
        .order_by("-finished_at", "-id")
        .first()
    )
    if row is None:
        return None
    if row.finished_at and max_age_hours > 0:
        age = (timezone.now() - row.finished_at).total_seconds() / 3600.0
        if age > max_age_hours:
            return None
    return row


def forecast_symbol(
    symbol: str,
    *,
    as_of: date | None = None,
    interval: str = "1d",
    pred_len: int | None = None,
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    side: str = "BUY",
    force: bool = False,
    bars_df=None,
) -> KronosForecast:
    """Run or reuse a Kronos forecast; always returns a KronosForecast row."""
    as_of = as_of or today_ist()
    bare = symbol.upper().replace(".NS", "").replace(".BO", "").strip()
    settings_k = load_kronos_settings()
    plen = int(pred_len or settings_k["pred_len"])

    if not force:
        cached = get_cached(bare, as_of, interval=interval)
        if cached is not None:
            # Refresh stats if levels provided and differ
            if entry is not None and (stop is not None or target is not None):
                paths = (cached.config_snapshot or {}).get("paths") or []
                if paths:
                    stats = path_outcomes(
                        paths,
                        entry=float(entry),
                        stop=float(stop) if stop is not None else None,
                        target=float(target) if target is not None else None,
                        side=side,
                    )
                    cached.stats = stats
                    cached.call = stats.get("call") or cached.call
                    cached.entry = entry
                    cached.stop = stop
                    cached.target = target
                    cached.save(
                        update_fields=["stats", "call", "entry", "stop", "target", "updated_at"]
                    )
            return cached

    run = KronosForecast.objects.create(
        symbol=bare,
        as_of=as_of,
        interval=interval,
        status=KronosForecast.Status.RUNNING,
        model_id=settings_k["model_id"],
        lookback=settings_k["lookback"],
        pred_len=plen,
        config_snapshot={"settings": settings_k},
        entry=entry,
        stop=stop,
        target=target,
    )

    if not kronos_enabled():
        return _finish_error(run, "kronos disabled")
    if not kronos_sidecar_installed():
        return _finish_error(run, "kronos sidecar not installed")

    try:
        df = bars_df
        if df is None:
            from marketdata.service import MarketDataService

            svc = MarketDataService()
            # Indices need Yahoo codes (^NSEI), not NIFTY.NS / BANKNIFTY.NS
            from signals.tasks import FNO_YF_MAP

            hist_sym = (
                bare
                if bare.startswith("^")
                else (FNO_YF_MAP.get(bare) or f"{bare}.NS")
            )
            df = svc.bars_df(hist_sym, interval=interval)
            if df is None or df.empty:
                df = svc.bars_df(bare, interval=interval)
        bars = bars_to_payload(df, lookback=settings_k["lookback"])
        if len(bars) < 60:
            return _finish_error(run, f"insufficient bars ({len(bars)})")

        last_ts = bars[-1]["ts"]
        y_ts = next_session_timestamps(last_ts, plen, interval=interval)
        req = {
            "action": "predict",
            "bars": bars,
            "y_timestamps": y_ts,
            "pred_len": plen,
            "lookback": settings_k["lookback"],
            "paths": settings_k["paths"],
            "batch_chunk": settings_k["batch_chunk"],
            "T": settings_k["T"],
            "top_p": settings_k["top_p"],
            "seed": settings_k["seed"],
            "model_id": settings_k["model_id"],
            "tokenizer_id": settings_k["tokenizer_id"],
            "max_context": settings_k["max_context"],
            "device": settings_k["device"],
            "hf_cache": settings_k["hf_cache"],
        }
        result = invoke_kronos(req, timeout_sec=settings_k["timeout_sec"])
        if result.get("timeout"):
            run.status = KronosForecast.Status.TIMEOUT
            run.error = result.get("error") or "timeout"
            run.finished_at = timezone.now()
            run.save()
            return run
        if not result.get("ok"):
            return _finish_error(run, result.get("error") or "kronos predict failed")

        paths = result.get("paths") or []
        last_close = float(result.get("last_close") or bars[-1]["close"])
        use_entry = float(entry) if entry is not None else last_close
        stats = path_outcomes(
            paths,
            entry=use_entry,
            stop=float(stop) if stop is not None else None,
            target=float(target) if target is not None else None,
            side=side,
        )
        # Drop bulky paths from DB by default — keep summary only
        snap = {
            "settings": settings_k,
            "model_id": result.get("model_id"),
            "lookback": result.get("lookback"),
            "paths_n": result.get("paths_n"),
            "last_close": last_close,
            # Keep paths for level recompute within cache window (may be large)
            "paths": paths,
        }
        run.status = KronosForecast.Status.OK
        run.model_id = str(result.get("model_id") or settings_k["model_id"])
        run.lookback = int(result.get("lookback") or settings_k["lookback"])
        run.pred_len = plen
        run.paths_n = int(result.get("paths_n") or len(paths))
        run.stats = stats
        run.call = str(stats.get("call") or "NEUTRAL")
        run.config_snapshot = snap
        run.entry = use_entry
        run.stop = stop
        run.target = target
        run.error = ""
        run.finished_at = timezone.now()
        run.save()
        return run
    except Exception as exc:  # noqa: BLE001
        logger.exception("kronos forecast failed for %s", bare)
        return _finish_error(run, str(exc))


def _finish_error(run: KronosForecast, message: str) -> KronosForecast:
    run.status = KronosForecast.Status.ERROR
    run.error = (message or "")[:2000]
    run.call = "NEUTRAL"
    run.stats = {"call": "NEUTRAL", "badge": "kronos_unavailable", "reason": message}
    run.finished_at = timezone.now()
    run.save()
    return run


def attach_kronos_to_signal(signal, *, force: bool = False) -> dict[str, Any]:
    """Write pipeline['kronos'] for a Signal; fail-open."""
    try:
        if not kronos_enabled():
            payload = _unavailable("kronos disabled")
        else:
            sym = signal.instrument.symbol
            interval = "5m" if (signal.product or "").upper() in {"MIS", "NRML"} and (
                signal.strategy or ""
            ).lower().startswith("fno") else "1d"
            # F&O on underlying daily is fine for direction; 5m only when bars exist
            run = forecast_symbol(
                sym,
                interval="1d" if interval == "5m" else interval,  # default daily for reliability
                entry=float(signal.entry_trigger or 0) or None,
                stop=float(signal.sl_trigger or 0) or None,
                target=float(signal.target1 or 0) or None,
                side=signal.side or "BUY",
                force=force,
            )
            if run.status != KronosForecast.Status.OK:
                payload = _unavailable(run.error or "kronos error")
            else:
                payload = {
                    "ok": True,
                    "badge": f"kronos_{str(run.call or 'neutral').lower()}",
                    "call": run.call,
                    "stats": run.stats or {},
                    "forecast_id": run.id,
                    "model_id": run.model_id,
                    "lookback": run.lookback,
                    "paths_n": run.paths_n,
                    "pred_len": run.pred_len,
                    "parameters": {
                        "model_id": run.model_id,
                        "lookback": run.lookback,
                        "paths": run.paths_n,
                        "pred_len": run.pred_len,
                        "T": (run.config_snapshot or {}).get("settings", {}).get("T"),
                        "top_p": (run.config_snapshot or {}).get("settings", {}).get("top_p"),
                        "seed": (run.config_snapshot or {}).get("settings", {}).get("seed"),
                    },
                }
    except Exception as exc:  # noqa: BLE001
        logger.exception("attach_kronos_to_signal failed")
        payload = _unavailable(str(exc))

    pipe = dict(signal.pipeline or {})
    pipe["kronos"] = payload
    signal.pipeline = pipe
    signal.save(update_fields=["pipeline"])
    return payload


def forecast_universe(symbols: list[str], *, interval: str = "1d") -> list[KronosForecast]:
    out: list[KronosForecast] = []
    for sym in symbols:
        try:
            out.append(forecast_symbol(sym, interval=interval, force=False))
        except Exception:  # noqa: BLE001
            logger.exception("universe forecast failed for %s", sym)
    return out
