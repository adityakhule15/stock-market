from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from config import trading_config as cfg
from marketdata.holidays import is_trading_day
from marketdata.models import JobRun
from marketdata.service import MarketDataService
from marketdata.universe import ensure_seed_instruments, shortlist_symbols
from notifications.telegram import alert_signal, send_message
from portfolio.services import record_equity_snapshot

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _run_job(name: str, fn):
    if not is_trading_day() and name not in ("weekly_retrain", "drift_check"):
        logger.info("Skip %s — holiday/weekend", name)
        return
    jr = JobRun.objects.create(name=name)
    try:
        detail = fn() or ""
        jr.ok = True
        jr.detail = str(detail)[:2000]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed", name)
        jr.ok = False
        jr.detail = str(exc)[:2000]
    finally:
        jr.finished_at = timezone.now()
        jr.save()


def refresh_master():
    n = ensure_seed_instruments()
    return f"seeded={n}"


def premarket_plan():
    from signals.tasks import generate_signals_for_universe

    signals = generate_signals_for_universe()
    for s in signals:
        if s.status == "new":
            alert_signal(s)
    return f"signals={len(signals)}"


def poll_delayed_feed():
    svc = MarketDataService()
    symbols = shortlist_symbols(30)
    updated = 0
    for sym in symbols:
        try:
            df, source = svc.history(sym, interval="5m", period="5d", use_cache=False)
            svc.upsert_bars(sym, "5m", df, source)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("poll %s: %s", sym, exc)
    return f"updated={updated}"


def orb_plans():
    if not cfg.INTRADAY_ENABLED:
        return "disabled"
    from scanner.orb import orb_plan
    from marketdata.service import MarketDataService

    svc = MarketDataService()
    plans = []
    for sym in shortlist_symbols(20):
        try:
            df, _ = svc.history(sym, interval="5m", period="5d", use_cache=False)
            today = datetime.now(IST).date()
            day = df[df.index.date == today] if hasattr(df.index, "date") else df
            if day.empty:
                continue
            prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else float(day["close"].iloc[0])
            plan = orb_plan(sym, day, avg_or_volume=float(day["volume"].mean() or 1), prev_close=prev_close, nifty_ok=True)
            if plan:
                plans.append(plan)
                send_message(f"ORB plan {sym}: trigger {plan['entry_trigger']} stop {plan['sl_trigger']} qty {plan['qty']}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("orb %s: %s", sym, exc)
    return f"plans={len(plans)}"


def eod_ingest():
    svc = MarketDataService()
    results = svc.backfill_many(shortlist_symbols(50), years=1)
    record_equity_snapshot()
    return f"ok={sum(1 for v in results.values() if v)}"


def nightly_retrain():
    from ml.meta_filter import maybe_promote_meta
    from ml.retrain import maybe_auto_rollback, retrain_and_maybe_promote

    mv, promoted = retrain_and_maybe_promote(full=False)
    rolled = maybe_auto_rollback()
    meta = {}
    try:
        meta = maybe_promote_meta()
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta retrain failed")
        meta = {"promoted": False, "reason": str(exc)[:200]}
    return (
        f"v{mv.version} promoted={promoted} rollback={rolled} "
        f"meta_promoted={meta.get('promoted')} meta_n={meta.get('n', 0)} "
        f"meta={meta.get('reason', '')[:80]}"
    )


def weekly_retrain():
    from ml.retrain import retrain_and_maybe_promote

    mv, promoted = retrain_and_maybe_promote(full=True, n_trials=20)
    return f"v{mv.version} promoted={promoted}"


def drift_check():
    from features.builders import build_features, feature_matrix, build_dataset
    from ml.drift import check_drift
    from marketdata.service import MarketDataService

    svc = MarketDataService()
    sym = shortlist_symbols(1)
    if not sym:
        return "no symbols"
    df = svc.bars_df(sym[0], interval="1d")
    ds = build_dataset(df)
    if len(ds) < 100:
        return "insufficient"
    X, _ = feature_matrix(ds)
    ref, cur = X.iloc[:-60], X.iloc[-60:]
    rep = check_drift(ref, cur)
    if rep.triggered_retrain:
        nightly_retrain()
    return f"psi={rep.psi_max:.3f}"


def square_off_reminder():
    if not cfg.INTRADAY_ENABLED:
        return "disabled"
    send_message("StockScope: square off any open MIS by 15:10 IST")
    return "sent"


def cancel_orb_reminder():
    if not cfg.INTRADAY_ENABLED:
        return "disabled"
    send_message("StockScope: cancel untriggered ORB pending orders")
    return "sent"


def daily_summary_1545():
    """15:45 IST — P&L, mistakes, tomorrow watchlist via Telegram + log."""
    from datetime import date

    from portfolio.models import PortfolioSettings, Position, TradeReview
    from portfolio.reviews import top_mistake_tags

    ps = PortfolioSettings.get_solo()
    today = date.today()
    closed = Position.objects.filter(status=Position.Status.CLOSED, closed_at__date=today)
    pnl = sum(float(p.pnl) for p in closed)
    tags = top_mistake_tags(limit=20)
    tag_txt = ", ".join(f"{t}×{n}" for t, n in tags[:3]) or "none"
    watch = ", ".join(ps.fno_watchlist or []) or "—"
    msg = (
        f"StockScope daily summary {today.isoformat()}\n"
        f"Day P&L ₹{float(ps.day_pnl):.2f} (closed net ₹{pnl:.2f})\n"
        f"Equity ₹{float(ps.equity):.2f} · life {ps.life_status}\n"
        f"Mistakes: {tag_txt}\n"
        f"Tomorrow watchlist: {watch}"
    )
    send_message(msg)
    return "sent"
