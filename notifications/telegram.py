from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        # Optional channel — stay quiet when unset (no console spam)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram send failed: %s", exc)
        return False


def alert_signal(signal) -> bool:
    tm = signal.time_machine or {}
    gate = signal.gate or {}
    text = (
        f"StockScope {signal.side} {signal.instrument.symbol}\n"
        f"Entry SL {signal.entry_trigger}/{signal.entry_limit} qty {signal.qty}\n"
        f"Stop {signal.sl_trigger} T1 {signal.target1}\n"
        f"Prob {signal.probability:.2f} RR_net {signal.rr_net:.2f}\n"
        f"TM {tm.get('verdict')} Gate {gate.get('result')}\n"
        f"Data age {signal.data_age_min}m source {signal.data_source}"
    )
    return send_message(text)
