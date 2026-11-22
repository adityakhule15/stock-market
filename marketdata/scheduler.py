"""Shared APScheduler job registration — used by run_worker and embedded run."""

from __future__ import annotations

import logging
import os
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from marketdata import jobs

logger = logging.getLogger(__name__)

_scheduler = None
_lock = threading.Lock()


def _register(sched) -> None:
    def wrap(name, fn):
        return lambda: jobs._run_job(name, fn)

    sched.add_job(wrap("refresh_master", jobs.refresh_master), CronTrigger(day_of_week="mon-fri", hour=8, minute=30))
    sched.add_job(wrap("premarket_plan", jobs.premarket_plan), CronTrigger(day_of_week="mon-fri", hour=8, minute=45))
    sched.add_job(
        wrap("poll_delayed_feed", jobs.poll_delayed_feed),
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5"),
    )
    sched.add_job(wrap("orb_plans", jobs.orb_plans), CronTrigger(day_of_week="mon-fri", hour=9, minute=46))
    sched.add_job(
        wrap("cancel_orb_reminder", jobs.cancel_orb_reminder),
        CronTrigger(day_of_week="mon-fri", hour=14, minute=0),
    )
    sched.add_job(
        wrap("square_off_reminder", jobs.square_off_reminder),
        CronTrigger(day_of_week="mon-fri", hour=15, minute=10),
    )
    sched.add_job(
        wrap("daily_summary_1545", jobs.daily_summary_1545),
        CronTrigger(day_of_week="mon-fri", hour=15, minute=45),
    )
    sched.add_job(wrap("eod_ingest", jobs.eod_ingest), CronTrigger(day_of_week="mon-fri", hour=16, minute=0))
    # TradingAgents memory settlement — after eod_ingest, before retrain
    sched.add_job(
        wrap("agents_settle_memory", jobs.agents_settle_memory),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15),
    )
    sched.add_job(
        wrap("nightly_retrain", jobs.nightly_retrain),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30),
    )
    # Kronos daily precompute after retrain (CPU; fail-open if disabled)
    sched.add_job(
        wrap("kronos_nightly", jobs.kronos_nightly),
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0),
    )
    sched.add_job(wrap("weekly_retrain", jobs.weekly_retrain), CronTrigger(day_of_week="sat", hour=10, minute=0))
    sched.add_job(wrap("drift_check", jobs.drift_check), CronTrigger(minute=5))


def start_blocking() -> None:
    sched = BlockingScheduler(timezone="Asia/Kolkata")
    _register(sched)
    sched.start()


def start_embedded() -> BackgroundScheduler | None:
    """Start background scheduler once (web + jobs in one process)."""
    global _scheduler
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
    if os.environ.get("DISABLE_SCHEDULER", "") == "1":
        logger.info("Scheduler disabled via DISABLE_SCHEDULER=1")
        return None
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return _scheduler
        sched = BackgroundScheduler(timezone="Asia/Kolkata")
        _register(sched)
        sched.start()
        _scheduler = sched
        logger.info("Embedded APScheduler started (Asia/Kolkata) — %s jobs", len(sched.get_jobs()))
        return sched
