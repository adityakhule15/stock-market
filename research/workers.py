"""Background execution for research runs — never block HTTP requests."""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending: set[int] = set()


def enqueue_research_run(run_id: int) -> None:
    with _lock:
        if run_id in _pending:
            return
        _pending.add(run_id)

    def _job():
        try:
            from research.services import execute_research_run

            execute_research_run(run_id)
        except Exception:  # noqa: BLE001
            logger.exception("background research run %s failed", run_id)
        finally:
            with _lock:
                _pending.discard(run_id)

    t = threading.Thread(target=_job, name=f"research-run-{run_id}", daemon=True)
    t.start()


def enqueue_callable(name: str, fn: Callable[[], None]) -> None:
    def _job():
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("background job %s failed", name)

    threading.Thread(target=_job, name=name, daemon=True).start()
