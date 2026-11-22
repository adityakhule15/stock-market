"""SEBI PIT insider-trading disclosures via NSE (trader-pelosi India equivalent).

Uses public NSE endpoints only. Fail-open. Never sends broker tokens.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import requests

from marketdata.events import _CACHE, _NSE, _dmy, _session

logger = logging.getLogger(__name__)

_INSIDER_TTL = 6 * 3600.0
_ALLOWED_CATEGORIES = {
    "promoter",
    "promoter group",
    "promoters",
    "director",
    "key managerial personnel",
    "kmp",
    "designated person",
}
_SKIP_MODES = {
    "esop",
    "gift",
    "inheritance",
    "transmission",
    "inter-se",
    "inter se",
    "off market",
    "off-market",
    "pledge",
}


def _cache_get(key: str, ttl: float = _INSIDER_TTL):
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > ttl:
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


def list_pit_filings(
    symbol: str,
    *,
    lookback_days: int = 30,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """List NSE corporates-pit-gg filings for a symbol."""
    as_of = as_of or date.today()
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    key = f"pit:{bare}:{lookback_days}:{as_of.isoformat()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    start = as_of - timedelta(days=lookback_days)
    rows: list[dict[str, Any]] = []
    try:
        s = _session()
        r = s.get(
            f"{_NSE}/api/corporates-pit-gg",
            params={
                "index": "equities",
                "from_date": _dmy(start),
                "to_date": _dmy(as_of),
                "symbol": bare,
            },
            timeout=15,
        )
        if r.ok:
            data = r.json()
            if isinstance(data, dict):
                rows = data.get("data") or []
            elif isinstance(data, list):
                rows = data
            # Filter to symbol (endpoint may ignore symbol sometimes)
            rows = [x for x in rows if str(x.get("symbol") or "").upper() == bare]
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_pit_filings %s: %s", bare, exc)
        rows = []
    _cache_set(key, rows)
    return rows


def fetch_xbrl(xml_url: str, *, session: requests.Session | None = None) -> str:
    if not xml_url:
        return ""
    url = xml_url if xml_url.startswith("http") else urljoin(_NSE + "/", xml_url.lstrip("/"))
    key = f"xbrl:{url}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    s = session or _session()
    try:
        r = s.get(url, timeout=20)
        text = r.text if r.ok else ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetch_xbrl %s: %s", url, exc)
        text = ""
    _cache_set(key, text)
    return text


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_pit_xbrl(xml_text: str) -> list[dict[str, Any]]:
    """Parse SEBI PIT XBRL into transaction dicts (stdlib ElementTree)."""
    if not (xml_text or "").strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Group by contextRef
    by_ctx: dict[str, dict[str, str]] = {}
    for el in root.iter():
        name = _local(el.tag)
        ctx = el.attrib.get("contextRef") or ""
        if not ctx or el.text is None:
            continue
        by_ctx.setdefault(ctx, {})[name] = (el.text or "").strip()

    # Field aliases seen in NSE IT_* XML files
    out: list[dict[str, Any]] = []
    for ctx, facts in by_ctx.items():
        if not ctx.startswith("Disclosure"):
            continue
        person = (
            facts.get("NameOfThePerson")
            or facts.get("NameOfThePersonOrEntity")
            or ""
        )
        category = (
            facts.get("CategoryOfPerson")
            or facts.get("CategoryOfPersonOrEntity")
            or ""
        )
        txn_type = (
            facts.get("SecuritiesAcquiredOrDisposedTransactionType")
            or facts.get("TransactionType")
            or ""
        )
        mode = (
            facts.get("ModeOfAcquisitionOrDisposal")
            or facts.get("ModeOfAcquisition")
            or ""
        )
        qty_raw = (
            facts.get("SecuritiesAcquiredOrDisposedNumberOfSecurity")
            or facts.get("NumberOfSecurities")
            or "0"
        )
        val_raw = (
            facts.get("SecuritiesAcquiredOrDisposedValueOfSecurity")
            or facts.get("ValueOfSecurities")
            or "0"
        )
        try:
            qty = float(re.sub(r"[^\d.\-]", "", qty_raw) or 0)
        except ValueError:
            qty = 0.0
        try:
            value = float(re.sub(r"[^\d.\-]", "", val_raw) or 0)
        except ValueError:
            value = 0.0

        out.append(
            {
                "context": ctx,
                "person": person,
                "category": category,
                "type": txn_type,
                "mode": mode,
                "quantity": qty,
                "value_inr": value,
            }
        )
    return out


def _is_market_buy_sell(txn: dict[str, Any]) -> bool:
    cat = str(txn.get("category") or "").lower()
    if not any(c in cat for c in _ALLOWED_CATEGORIES):
        return False
    mode = str(txn.get("mode") or "").lower()
    if any(s in mode for s in _SKIP_MODES):
        return False
    t = str(txn.get("type") or "").lower()
    return ("buy" in t) or ("purchase" in t) or ("sell" in t) or ("sale" in t)


def _signed_value(txn: dict[str, Any]) -> float:
    t = str(txn.get("type") or "").lower()
    v = float(txn.get("value_inr") or 0)
    if "sell" in t or "sale" in t:
        return -abs(v)
    return abs(v)


def summarize_insider(
    symbol: str,
    *,
    lookback_days: int = 30,
    as_of: date | None = None,
    max_filings: int = 5,
) -> dict[str, Any]:
    """Net promoter/director/KMP market flow over the lookback window."""
    as_of = as_of or date.today()
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    filings = list_pit_filings(bare, lookback_days=lookback_days, as_of=as_of)
    s = _session()
    txns: list[dict[str, Any]] = []
    pledge_events = 0
    for f in filings[:max_filings]:
        xml_name = f.get("xmlFileName") or f.get("xbrl") or ""
        text = fetch_xbrl(xml_name, session=s)
        parsed = parse_pit_xbrl(text)
        for t in parsed:
            mode = str(t.get("mode") or "").lower()
            if "pledge" in mode:
                pledge_events += 1
                continue
            if _is_market_buy_sell(t):
                txns.append(t)

    net = sum(_signed_value(t) for t in txns)
    buys = sum(_signed_value(t) for t in txns if _signed_value(t) > 0)
    sells = -sum(_signed_value(t) for t in txns if _signed_value(t) < 0)

    if pledge_events and net == 0 and not txns:
        flag = "pledge"
        vote = -1.0
        label = "promoter pledge activity"
    elif net > 0:
        flag = "buy"
        vote = 0.5
        label = "net insider buying"
    elif net < 0:
        flag = "sell"
        vote = -0.5
        label = "net insider selling"
    else:
        flag = "none"
        vote = None
        label = "no insider activity"

    return {
        "symbol": bare,
        "lookback_days": lookback_days,
        "filings_n": len(filings),
        "txns_n": len(txns),
        "net_value_inr": round(net, 2),
        "buy_value_inr": round(buys, 2),
        "sell_value_inr": round(sells, 2),
        "pledge_events": pledge_events,
        "flag": flag,
        "vote": vote,
        "label": label,
        "as_of": as_of.isoformat(),
    }
