"""NSE event calendar / results / announcements (cached, best-effort)."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 3600.0
_NSE = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockScope/1.0)",
    "Accept": "application/json",
}


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > _TTL:
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:
        s.get(_NSE, timeout=8)
    except Exception:  # noqa: BLE001
        pass
    return s


def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:11] if " " in raw else raw, fmt).date()
        except ValueError:
            continue
    return None


def announcements_for(symbol: str, lookback_days: int = 3) -> list[dict]:
    key = f"ann:{symbol}:{lookback_days}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    end = date.today()
    start = end - timedelta(days=lookback_days)
    rows: list[dict] = []
    try:
        s = _session()
        r = s.get(
            f"{_NSE}/api/corporate-announcements",
            params={"index": "equities", "from_date": _dmy(start), "to_date": _dmy(end), "symbol": symbol},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                rows = data.get("data") or data.get("announcements") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("announcements_for %s: %s", symbol, exc)
    _cache_set(key, rows)
    return rows


def next_results_date(symbol: str, as_of: date | None = None) -> date | None:
    as_of = as_of or date.today()
    key = f"res:{symbol}:{as_of.isoformat()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    end = as_of + timedelta(days=14)
    best: date | None = None
    try:
        s = _session()
        for path in ("api/event-calendar", "api/corporate-board-meetings"):
            r = s.get(
                f"{_NSE}/{path}",
                params={"index": "equities", "from_date": _dmy(as_of), "to_date": _dmy(end)},
                timeout=10,
            )
            if not r.ok:
                continue
            data = r.json()
            rows = data if isinstance(data, list) else (data.get("data") or [])
            for row in rows:
                sym = str(row.get("symbol") or row.get("sm_symbol") or "").upper()
                if sym != symbol.upper():
                    continue
                d = _parse_date(row.get("date") or row.get("bm_date") or row.get("meetingDate"))
                purpose = str(row.get("purpose") or row.get("subject") or "").lower()
                if d and ("result" in purpose or "financial" in purpose or "board" in purpose or True):
                    if d >= as_of and (best is None or d < best):
                        best = d
    except Exception as exc:  # noqa: BLE001
        logger.debug("next_results_date %s: %s", symbol, exc)
    _cache_set(key, best)
    return best


def event_flags(as_of: date | None = None) -> dict[str, Any]:
    """Market-wide flags: F&O expiry Thursday, rough RBI/budget placeholders."""
    as_of = as_of or date.today()
    # Equity derivatives weekly expiry is typically Thursday
    is_fno_expiry = as_of.weekday() == 3
    # Static-ish special days (extend later); empty means none known
    special = []
    # First Wednesday of some months often RBI — heuristic only
    if as_of.weekday() == 2 and 1 <= as_of.day <= 7:
        special.append("possible_rbi_day")
    return {
        "as_of": as_of.isoformat(),
        "fno_expiry": is_fno_expiry,
        "special": special,
        "warn": is_fno_expiry or bool(special),
        "reason": (
            "F&O expiry day — caution"
            if is_fno_expiry
            else ("event day" if special else "no major event flag")
        ),
    }
