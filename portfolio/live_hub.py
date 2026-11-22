"""CPU-light live marks hub.

Design (SSE-friendly, no Django Channels):
- One background refresher while ≥1 subscriber is connected
- One option-chain fetch per underlying (shared across positions)
- Skip network work when nobody is watching / no open positions
- Push only when the fingerprint changes (identical LTP frames are dropped)

SSE is used instead of WebSockets: one-way market marks need no client→server
frames, auto-reconnect is built into EventSource, and we avoid Channels/Redis.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

REFRESH_SEC = 10.0
IDLE_SLEEP_SEC = 25.0
MAX_CHAIN_AGE_SEC = 50


def _settings_refresh_sec() -> float:
    try:
        from portfolio.models import PortfolioSettings

        sec = int(PortfolioSettings.get_solo().live_refresh_sec or 10)
        return float(max(5, min(120, sec)))
    except Exception:  # noqa: BLE001
        return float(REFRESH_SEC)


def set_refresh_sec(sec: int | float) -> float:
    """Update module refresh interval (also persisted via PortfolioSettings API)."""
    global REFRESH_SEC
    REFRESH_SEC = float(max(5, min(120, int(sec))))
    return REFRESH_SEC


class LiveMarksHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._subscribers = 0
        self._snapshot: dict[str, Any] | None = None
        self._fingerprint = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_error = ""

    # —— subscriber lifecycle ——
    def add_subscriber(self) -> None:
        with self._cv:
            self._subscribers += 1
            self._ensure_thread_locked()
            self._cv.notify_all()

    def remove_subscriber(self) -> None:
        with self._cv:
            self._subscribers = max(0, self._subscribers - 1)
            self._cv.notify_all()

    @property
    def subscribers(self) -> int:
        with self._lock:
            return self._subscribers

    def latest(self) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            return self._snapshot, self._fingerprint

    def wait_update(self, last_fp: str, timeout: float = 15.0) -> tuple[dict[str, Any] | None, str]:
        """Block until fingerprint changes or timeout. Returns (snapshot, fp)."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._fingerprint == last_fp or self._snapshot is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._snapshot, self._fingerprint
                self._cv.wait(timeout=remaining)
            return self._snapshot, self._fingerprint

    def force_refresh(self) -> dict[str, Any]:
        snap = self._build_snapshot()
        self._publish(snap)
        return snap

    def _ensure_thread_locked(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(target=self._loop, name="stockscope-live-hub", daemon=True)
        self._thread = t
        t.start()
        logger.info("Live marks hub started")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                from django.db import close_old_connections

                close_old_connections()
            except Exception:  # noqa: BLE001
                pass

            with self._lock:
                subs = self._subscribers

            if subs <= 0:
                # No viewers — park the thread lightly; wake if someone joins
                with self._cv:
                    self._cv.wait(timeout=IDLE_SLEEP_SEC)
                with self._lock:
                    if self._subscribers <= 0:
                        # Stay alive but cheap; exit only after long idle to avoid churn
                        continue
                continue

            try:
                snap = self._build_snapshot()
                self._publish(snap)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)[:200]
                logger.warning("Live hub refresh failed: %s", exc)

            # Sleep, but wake early on new subscriber / stop
            with self._cv:
                self._cv.wait(timeout=_settings_refresh_sec())

    def _publish(self, snap: dict[str, Any]) -> None:
        fp = _fingerprint(snap)
        with self._cv:
            changed = fp != self._fingerprint
            self._snapshot = snap
            if changed:
                self._fingerprint = fp
                self._cv.notify_all()

    def _build_snapshot(self) -> dict[str, Any]:
        from portfolio.live import enrich_position, fno_meta, is_fno
        from portfolio.models import PortfolioSettings, Position
        from portfolio.services import repair_open_fno_reservations

        # Cheap reservation fix once in a while is fine; skip if notes already set
        try:
            repair_open_fno_reservations()
        except Exception:  # noqa: BLE001
            pass

        qs = list(
            Position.objects.filter(status=Position.Status.OPEN)
            .select_related("instrument", "signal")
            .prefetch_related("fills")
            .order_by("-opened_at")[:100]
        )

        chains: dict[str, dict] = {}
        underlyings: set[str] = set()
        equity_syms: set[str] = set()
        for pos in qs:
            if is_fno(pos):
                meta = fno_meta(pos) or {}
                underlyings.add(str(meta.get("underlying") or pos.instrument.symbol).upper())
            else:
                equity_syms.add(pos.instrument.symbol.upper())

        if underlyings:
            from marketdata.option_chain import get_chain_cached_or_live

            # Wide strike window so deep OTM opens (e.g. 26500 CE) still get LTP
            for u in underlyings:
                try:
                    chains[u] = get_chain_cached_or_live(
                        u, expiry=None, max_age_sec=MAX_CHAIN_AGE_SEC, strike_count=50
                    )
                except Exception as exc:  # noqa: BLE001
                    chains[u] = {"rows": [], "error": str(exc)[:120], "symbol": u}

        if equity_syms:
            try:
                from marketdata.service import MarketDataService

                svc = MarketDataService()
                for sym in equity_syms:
                    try:
                        yf = f"{sym}.NS"
                        df = svc.bars_df(yf, interval="1d")
                        if df is not None and len(df):
                            chains[f"__eq__:{sym}"] = {
                                "ltp": float(df["close"].iloc[-1]),
                                "source": "bars_1d",
                            }
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

        rows: list[dict[str, Any]] = []
        open_pnl_total = 0.0
        open_pnl_net_total = 0.0
        for pos in qs:
            try:
                row = enrich_position(pos, chains=chains)
            except Exception as exc:  # noqa: BLE001
                row = {
                    "id": pos.id,
                    "symbol": pos.instrument.symbol,
                    "status": pos.status,
                    "error": str(exc)[:160],
                    "qty_label": str(pos.qty),
                    "suggestion": {"code": "WAIT", "label": "WAIT", "detail": str(exc)[:120]},
                }
            row["is_dummy"] = bool(getattr(pos, "is_dummy", False))
            rows.append(row)
            if row.get("is_dummy"):
                continue
            if row.get("open_pnl") is not None:
                open_pnl_total += float(row["open_pnl"])
            if row.get("open_pnl_net") is not None:
                open_pnl_net_total += float(row["open_pnl_net"])

        # Today = open MTM + realized on positions closed today
        from portfolio.day_book import today_closed_realized

        closed_today = today_closed_realized()
        today_pnl_gross = round(open_pnl_total + float(closed_today["gross_pnl"]), 2)
        today_pnl_net = round(open_pnl_net_total + float(closed_today["net_pnl"]), 2)

        ps = PortfolioSettings.get_solo()
        return {
            "ok": True,
            "transport": "sse",
            "rows": rows,
            "open_pnl_total": round(open_pnl_total, 2),
            "open_pnl_net_total": round(open_pnl_net_total, 2),
            "closed_today_gross": float(closed_today["gross_pnl"]),
            "closed_today_net": float(closed_today["net_pnl"]),
            "today_pnl_gross": today_pnl_gross,
            "today_pnl_net": today_pnl_net,
            "equity_mark": round(float(ps.equity) + open_pnl_total, 2),
            "cash": float(ps.cash),
            "equity_book": float(ps.equity),
            "as_of": timezone.localtime().strftime("%d-%b-%Y %H:%M:%S IST"),
            "underlyings_fetched": sorted(underlyings),
            "subscribers": self._subscribers,
            "refresh_sec": _settings_refresh_sec(),
        }


def _fingerprint(snap: dict[str, Any]) -> str:
    slim = []
    for r in snap.get("rows") or []:
        sug = (r.get("suggestion") or {}).get("code")
        slim.append((r.get("id"), r.get("ltp"), r.get("open_pnl"), sug, r.get("qty_label")))
    payload = json.dumps(
        {"rows": slim, "open": snap.get("open_pnl_total"), "cash": snap.get("cash")},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


_hub: LiveMarksHub | None = None
_hub_lock = threading.Lock()


def get_hub() -> LiveMarksHub:
    global _hub
    with _hub_lock:
        if _hub is None:
            _hub = LiveMarksHub()
        return _hub
