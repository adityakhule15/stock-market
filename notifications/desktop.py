"""Windows desktop toast alerts (HOLD / EXIT NOW / raise target / …).

Uses PowerShell balloon / toast — no extra pip deps. Safe no-op on non-Windows.
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Human titles for Windows / browser notifications
ACTION_TITLES = {
    "EXIT_NOW": "EXIT NOW — sell / square off",
    "EXIT_SOON": "Exit soon — prepare to sell",
    "SELL": "SELL — exit now",
    "RAISE_TARGET": "Increase the target",
    "TRAIL_SL": "Trail the stop",
    "MOVE_SL_BREAKEVEN": "Move SL to breakeven",
    "BOOK_PARTIAL": "Book partial profits",
    "HOLD": "Hold",
    "WAIT": "Wait",
}

# Actions that should pop a Windows alert (skip quiet HOLD spam)
ALERT_ACTIONS = frozenset(
    {
        "EXIT_NOW",
        "EXIT_SOON",
        "SELL",
        "RAISE_TARGET",
        "TRAIL_SL",
        "MOVE_SL_BREAKEVEN",
        "BOOK_PARTIAL",
    }
)


def friendly_title(action: str) -> str:
    code = str(action or "").strip().upper().replace(" ", "_")
    return ACTION_TITLES.get(code, str(action or "StockScope alert"))


def should_alert(action: str) -> bool:
    code = str(action or "").strip().upper().replace(" ", "_")
    return code in ALERT_ACTIONS


def _sanitize(text: str, *, limit: int = 180) -> str:
    s = re.sub(r"[\r\n\t]+", " ", str(text or "")).strip()
    s = s.replace("'", "").replace('"', "").replace("`", "")
    return s[:limit]


def _show_windows_toast(title: str, body: str) -> bool:
    if platform.system() != "Windows":
        return False
    title_s = _sanitize(title, limit=90)
    body_s = _sanitize(body, limit=200)
    if not title_s:
        return False
    # Balloon tip via WinForms — works on Win10/11 without BurntToast module
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Warning
$n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
$n.BalloonTipTitle = '{title_s}'
$n.BalloonTipText = '{body_s or title_s}'
$n.Visible = $true
$n.ShowBalloonTip(10000)
Start-Sleep -Seconds 11
$n.Dispose()
"""
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-Command",
                ps,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Windows toast failed: %s", exc)
        return False


def notify_windows(title: str, body: str = "", *, async_: bool = True) -> bool:
    """Fire a Windows notification. Non-blocking by default."""
    if platform.system() != "Windows":
        return False
    if async_:
        threading.Thread(
            target=_show_windows_toast,
            args=(title, body),
            daemon=True,
            name="ss-win-toast",
        ).start()
        return True
    return _show_windows_toast(title, body)


def alert_management(
    *,
    action: str,
    reason: str = "",
    symbol: str = "",
    position_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Desktop + Telegram alert when management action changes."""
    code = str(action or "").strip().upper().replace(" ", "_")
    title = friendly_title(code)
    sym = str(symbol or "").upper()
    head = f"#{position_id} {sym}".strip() if position_id else sym
    body_bits = [head, reason]
    if extra:
        if extra.get("suggested_sl") is not None:
            body_bits.append(f"SL ₹{extra['suggested_sl']}")
        if extra.get("suggested_target") is not None:
            body_bits.append(f"Tgt ₹{extra['suggested_target']}")
    body = " · ".join(str(b) for b in body_bits if b)

    out = {"action": code, "title": title, "body": body, "windows": False, "telegram": False}
    if not should_alert(code):
        return out

    out["windows"] = notify_windows(f"StockScope · {title}", body)

    try:
        from notifications.telegram import send_message

        out["telegram"] = bool(
            send_message(f"StockScope {title}\n{body}".strip())
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("telegram mgmt alert skipped: %s", exc)

    return out
