"""Live IST session stage for Today board — never trust frozen scan-time labels alone."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from signals.engine import session_bounds

IST = ZoneInfo("Asia/Kolkata")

# Stages (ordered by severity for comparisons)
PRE_OPEN = "PRE_OPEN"
ENTER = "ENTER"
NO_NEW_ENTRY = "NO_NEW_ENTRY"
SQUARE_OFF = "SQUARE_OFF"
CLOSED = "CLOSED"

_ORDER = (PRE_OPEN, ENTER, NO_NEW_ENTRY, SQUARE_OFF, CLOSED)


def _now_ist(now: datetime | None = None) -> datetime:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def ist_session_stage(now: datetime | None = None) -> dict[str, Any]:
    """Return current NSE-style stage + banner/copy for the Today board."""
    now = _now_ist(now)
    bounds = session_bounds(now)
    t = now.time()
    weekend = now.weekday() >= 5

    if weekend or t >= time(15, 30):
        stage = CLOSED
        banner = (
            f"Stage: CLOSED · next session {bounds['session_label']} · "
            "use Buy for tomorrow · capital floors protected; P&L not guaranteed"
        )
        enter_ok = False
        fno_actionable = False
        force_expired = True
        emphasize_tomorrow = True
    elif t < time(9, 46):
        stage = PRE_OPEN
        banner = (
            f"Stage: PRE_OPEN · plan only · enter after 09:46 IST "
            f"({bounds['session_label']}) · capital floors protected; P&L not guaranteed"
        )
        enter_ok = False
        fno_actionable = False
        force_expired = False
        emphasize_tomorrow = False
    elif t <= time(14, 0):
        stage = ENTER
        banner = (
            f"Stage: ENTER · 09:46–14:00 IST · enter only when trigger hits · "
            "square-off F&O by 15:10 · capital floors protected; P&L not guaranteed"
        )
        enter_ok = True
        fno_actionable = True
        force_expired = False
        emphasize_tomorrow = False
    elif t < time(15, 10):
        stage = NO_NEW_ENTRY
        banner = (
            "Stage: NO_NEW_ENTRY · entry closed after 14:00 IST · do not buy · "
            "manage opens only · square-off by 15:10 · capital floors protected; P&L not guaranteed"
        )
        enter_ok = False
        fno_actionable = False
        force_expired = True
        emphasize_tomorrow = False
    else:
        stage = SQUARE_OFF
        banner = (
            "Stage: SQUARE_OFF · 15:10–15:30 IST · exit F&O now · no new tickets · "
            "capital floors protected; P&L not guaranteed"
        )
        enter_ok = False
        fno_actionable = False
        force_expired = True
        emphasize_tomorrow = True

    # Live wait/hold strings — after cutoff, do not echo today's dead 09:46–14:00 window as open
    if stage == ENTER:
        wait_fno = bounds["wait_range_fno"]
        wait_normal = bounds["wait_range"]
        hold_fno = bounds["hold_until_fno"]
        hold_normal = bounds["hold_until"]
        status_hint = "waiting"
    elif stage == PRE_OPEN:
        wait_fno = f"opens {bounds['session_label']} 09:46 IST"
        wait_normal = wait_fno
        hold_fno = bounds["hold_until_fno"]
        hold_normal = bounds["hold_until"]
        status_hint = "waiting"
    elif stage == NO_NEW_ENTRY:
        wait_fno = "entry closed (past 14:00 IST) — do not buy"
        wait_normal = wait_fno
        hold_fno = bounds["hold_until_fno"]
        hold_normal = bounds["hold_until"]
        status_hint = "expired"
    elif stage == SQUARE_OFF:
        wait_fno = "session square-off — exit F&O, no new buys"
        wait_normal = "no new entries after 14:00 — manage CNC opens"
        hold_fno = "EXIT NOW · 15:10 IST passed"
        hold_normal = bounds["hold_until"]
        status_hint = "expired"
    else:  # CLOSED
        wait_fno = f"next session {bounds['session_label']} 09:46–14:00 IST"
        wait_normal = wait_fno
        hold_fno = f"next session · square off by {bounds['session_label']} 15:10 IST"
        hold_normal = bounds["hold_until"]
        status_hint = "expired"

    return {
        "stage": stage,
        "banner": banner,
        "enter_ok": enter_ok,
        "fno_actionable": fno_actionable,
        "force_expired": force_expired,
        "emphasize_tomorrow": emphasize_tomorrow,
        "session_label": bounds["session_label"],
        "session_date": bounds["session_date"],
        "wait_range_fno": wait_fno,
        "wait_range": wait_normal,
        "hold_until_fno": hold_fno,
        "hold_until": hold_normal,
        "status_hint": status_hint,
        "now_ist": now.strftime("%d-%b-%Y %H:%M IST"),
        "order": list(_ORDER),
    }


def stage_at_least(stage: str, minimum: str) -> bool:
    try:
        return _ORDER.index(stage) >= _ORDER.index(minimum)
    except ValueError:
        return False


def apply_live_entry_status(
    *,
    stored: str | None,
    strategy: str,
    stage_info: dict[str, Any],
) -> str:
    """Recompute display/persist entry_status from IST stage (scan-time WAITING goes stale)."""
    stored = (stored or "waiting").lower()
    if stored == "triggered" and stage_info.get("enter_ok"):
        return "triggered"
    if stage_info.get("force_expired"):
        # Same-day F&O always expires after entry cutoff; Normal CNC expires for *new entry* only
        if strategy == "fno_intraday" or stage_info.get("stage") in {NO_NEW_ENTRY, SQUARE_OFF, CLOSED}:
            return "expired"
    return stored if stored in {"waiting", "triggered", "expired"} else "waiting"
