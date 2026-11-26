"""In-process log of why F&O / scan ideas were skipped — for Today empty-board reasons."""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_LAST: list[dict[str, Any]] = []
_MAX = 40


def clear() -> None:
    with _lock:
        _LAST.clear()


def record(kind: str, symbol: str, reason: str, **extra: Any) -> None:
    row = {"kind": kind, "symbol": symbol, "reason": reason, **extra}
    with _lock:
        _LAST.append(row)
        if len(_LAST) > _MAX:
            del _LAST[: len(_LAST) - _MAX]


def recent(*, kind: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    with _lock:
        rows = list(_LAST)
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    return rows[-limit:]


def summary_lines(limit: int = 8) -> list[str]:
    lines = []
    for r in recent(limit=limit):
        sym = r.get("symbol") or ""
        reason = r.get("reason") or ""
        kind = r.get("kind") or ""
        lines.append(f"{kind} {sym}: {reason}".strip())
    # unique preserve order
    out: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
    return out
