"""NSE live option-chain fetch + snapshot history (no API key).

Live: NSE option-chain-v3 (public site).
History: we store each successful fetch under data/option_chain/ so you build
a local history over time. Full multi-year option history is not free from NSE.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

_SESSION: requests.Session | None = None
_SESSION_TS = 0.0


def _cache_dir() -> Path:
    try:
        from django.conf import settings

        if settings.configured:
            return Path(settings.BASE_DIR) / "data" / "option_chain"
    except Exception:  # noqa: BLE001
        pass
    return Path(__file__).resolve().parents[1] / "data" / "option_chain"


def _session() -> requests.Session:
    global _SESSION, _SESSION_TS
    now = time.time()
    if _SESSION is None or now - _SESSION_TS > 600:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/option-chain",
            }
        )
        s.get("https://www.nseindia.com", timeout=15)
        s.get("https://www.nseindia.com/option-chain", timeout=15)
        _SESSION, _SESSION_TS = s, now
    return _SESSION


def normalize_symbol(symbol: str) -> str:
    s = symbol.upper().replace(".NS", "").replace("^NSEI", "NIFTY").replace("^NSEBANK", "BANKNIFTY")
    if s in {"NIFTY 50", "NIFTY50"}:
        return "NIFTY"
    return s


def list_expiries(symbol: str = "NIFTY") -> list[str]:
    symbol = normalize_symbol(symbol)
    s = _session()
    r = s.get(
        f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}",
        timeout=20,
    )
    r.raise_for_status()
    data = r.json() or {}
    return list(data.get("expiryDates") or [])


def _pick_upcoming_expiry(expiries: list[str]) -> str:
    today = datetime.now(IST).date()
    for e in expiries:
        try:
            d = datetime.strptime(e, "%d-%b-%Y").date()
        except ValueError:
            continue
        if d >= today:
            return e
    return expiries[0]


def fetch_option_chain(symbol: str = "NIFTY", expiry: str | None = None) -> dict[str, Any]:
    """Fetch live chain. expiry like '17-Nov-2026'. Defaults to nearest upcoming."""
    symbol = normalize_symbol(symbol)
    s = _session()
    expiries = list_expiries(symbol)
    if not expiries:
        raise RuntimeError(f"No expiries for {symbol}")
    if expiry is None:
        expiry = _pick_upcoming_expiry(expiries)
    is_index = symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
    type_q = "Indices" if is_index else "Equity"
    urls = [
        f"https://www.nseindia.com/api/option-chain-v3?symbol={symbol}&expiry={expiry}",
        f"https://www.nseindia.com/api/option-chain-v3?type={type_q}&symbol={symbol}&expiry={expiry}",
    ]
    raw: dict = {}
    last_err: Exception | None = None
    for url in urls:
        try:
            r = s.get(url, timeout=25)
            r.raise_for_status()
            raw = r.json() or {}
            if raw.get("records"):
                break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            raw = {}
    if not raw.get("records"):
        raise RuntimeError(f"NSE chain empty for {symbol} {expiry}: {last_err}")
    parsed = parse_chain(symbol, expiry, raw)
    save_snapshot(parsed)
    return parsed


def parse_chain(symbol: str, expiry: str, raw: dict) -> dict[str, Any]:
    records = (raw or {}).get("records") or {}
    rows_in = records.get("data") or []
    spot = float(records.get("underlyingValue") or 0)
    rows: list[dict] = []
    for r in rows_in:
        strike = float(r.get("strikePrice") or 0)
        if strike <= 0:
            continue
        ce = r.get("CE") or {}
        pe = r.get("PE") or {}
        rows.append(
            {
                "strike": strike,
                "ce_ltp": float(ce.get("lastPrice") or 0),
                "ce_oi": int(ce.get("openInterest") or 0),
                "ce_vol": int(ce.get("totalTradedVolume") or 0),
                "ce_iv": float(ce.get("impliedVolatility") or 0),
                "ce_bid": float(ce.get("buyPrice1") or 0),
                "ce_ask": float(ce.get("sellPrice1") or 0),
                "pe_ltp": float(pe.get("lastPrice") or 0),
                "pe_oi": int(pe.get("openInterest") or 0),
                "pe_vol": int(pe.get("totalTradedVolume") or 0),
                "pe_iv": float(pe.get("impliedVolatility") or 0),
                "pe_bid": float(pe.get("buyPrice1") or 0),
                "pe_ask": float(pe.get("sellPrice1") or 0),
            }
        )
    rows.sort(key=lambda x: x["strike"])
    now = datetime.now(IST)
    return {
        "symbol": symbol,
        "expiry": expiry,
        "spot": spot,
        "as_of": now.isoformat(),
        "as_of_label": now.strftime("%d-%b-%Y %H:%M IST"),
        "source": "nse_option_chain_v3",
        "n_strikes": len(rows),
        "rows": rows,
    }


def save_snapshot(chain: dict) -> Path:
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    sym = chain.get("symbol", "X")
    exp = str(chain.get("expiry", "exp")).replace(" ", "-")
    path = cache / f"{sym}_{exp}_{ts}.json"
    latest = cache / f"{sym}_{exp}_latest.json"
    payload = json.dumps(chain, indent=0)
    path.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return path


def load_latest_snapshot(symbol: str, expiry: str | None = None) -> dict | None:
    symbol = normalize_symbol(symbol)
    cache = _cache_dir()
    if not cache.exists():
        return None
    if expiry:
        p = cache / f"{symbol}_{expiry.replace(' ', '-')}_latest.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    files = sorted(cache.glob(f"{symbol}_*_latest.json"), reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))


def list_snapshots(symbol: str = "NIFTY", limit: int = 20) -> list[dict]:
    """Local history index (from our saved snapshots)."""
    symbol = normalize_symbol(symbol)
    cache = _cache_dir()
    if not cache.exists():
        return []
    out = []
    for p in sorted(cache.glob(f"{symbol}_*_20*.json"), reverse=True):
        if p.name.endswith("_latest.json"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "file": p.name,
                    "symbol": data.get("symbol"),
                    "expiry": data.get("expiry"),
                    "spot": data.get("spot"),
                    "as_of": data.get("as_of"),
                    "as_of_label": data.get("as_of_label"),
                    "n_strikes": data.get("n_strikes"),
                }
            )
        except Exception:  # noqa: BLE001
            continue
        if len(out) >= limit:
            break
    return out


def filter_chain_to_wallet(
    chain: dict,
    *,
    lot_size: int,
    max_debit: float,
    option_type: str | None = None,
) -> dict:
    """Keep only strikes where LTP×lot ≤ max_debit (wallet). Drops unaffordable noise."""
    if not chain:
        return chain
    max_ltp = (max_debit / lot_size) if lot_size else 0.0
    opt = None
    if option_type:
        opt = "CE" if option_type.upper() in {"CE", "CALL"} else "PE"
    kept = []
    for r in chain.get("rows") or []:
        ce = float(r.get("ce_ltp") or 0)
        pe = float(r.get("pe_ltp") or 0)
        if opt == "CE":
            if ce <= 0 or ce > max_ltp + 1e-9:
                continue
        elif opt == "PE":
            if pe <= 0 or pe > max_ltp + 1e-9:
                continue
        else:
            # keep row if either side can fit
            ce_ok = ce > 0 and ce <= max_ltp + 1e-9
            pe_ok = pe > 0 and pe <= max_ltp + 1e-9
            if not (ce_ok or pe_ok):
                continue
            # zero out the side that does not fit so callers don't use it
            r = dict(r)
            if not ce_ok:
                r["ce_ltp"] = 0.0
            if not pe_ok:
                r["pe_ltp"] = 0.0
        kept.append(r)
    out = dict(chain)
    out["rows"] = kept
    out["n_strikes"] = len(kept)
    out["wallet_filtered"] = True
    out["max_debit"] = round(float(max_debit), 2)
    out["max_ltp"] = round(max_ltp, 2)
    return out


def pick_affordable_from_chain(
    chain: dict,
    option_type: str,
    *,
    lot_size: int,
    max_debit: float,
    min_volume: int = 100,
) -> dict | None:
    """Pick liquid OTM-ish strike with LTP×lot ≤ max_debit."""
    opt = "CE" if option_type.upper() in {"CE", "CALL"} else "PE"
    spot = float(chain.get("spot") or 0)
    max_ltp = max_debit / lot_size if lot_size else 0
    cands = []
    for r in chain.get("rows") or []:
        ltp = float(r["ce_ltp"] if opt == "CE" else r["pe_ltp"])
        vol = int(r["ce_vol"] if opt == "CE" else r["pe_vol"])
        oi = int(r["ce_oi"] if opt == "CE" else r["pe_oi"])
        strike = float(r["strike"])
        if ltp <= 0 or ltp > max_ltp:
            continue
        if vol < min_volume and oi < 500:
            continue
        # Prefer OTM
        otm = (strike - spot) if opt == "CE" else (spot - strike)
        if otm < 0:
            continue  # skip ITM for debit budget trades
        score = vol * 2 + oi * 0.01 - abs(ltp - max_ltp * 0.7)
        cands.append(
            {
                "strike": strike,
                "ltp": round(ltp, 2),
                "debit": round(ltp * lot_size, 2),
                "oi": oi,
                "volume": vol,
                "otm": round(otm, 2),
                "score": score,
                "option_type": opt,
            }
        )
    if not cands:
        # relax volume filter
        for r in chain.get("rows") or []:
            ltp = float(r["ce_ltp"] if opt == "CE" else r["pe_ltp"])
            strike = float(r["strike"])
            if ltp <= 0 or ltp > max_ltp:
                continue
            otm = (strike - spot) if opt == "CE" else (spot - strike)
            if otm < 0:
                continue
            cands.append(
                {
                    "strike": strike,
                    "ltp": round(ltp, 2),
                    "debit": round(ltp * lot_size, 2),
                    "oi": int(r["ce_oi"] if opt == "CE" else r["pe_oi"]),
                    "volume": int(r["ce_vol"] if opt == "CE" else r["pe_vol"]),
                    "otm": round(otm, 2),
                    "score": ltp,
                    "option_type": opt,
                }
            )
    if not cands:
        return None
    cands.sort(key=lambda x: (-x["volume"], -x["oi"], x["ltp"]))
    best = cands[0]
    best["spot"] = spot
    best["expiry"] = chain.get("expiry")
    best["as_of_label"] = chain.get("as_of_label")
    best["source"] = chain.get("source")
    return best


def get_chain_cached_or_live(
    symbol: str,
    expiry: str | None = None,
    max_age_sec: int = 300,
    *,
    strike_count: int = 25,
) -> dict:
    """Prefer INDstocks live chain when token set; else NSE. Cache snapshots briefly.

    ``strike_count`` widens INDstocks window (default 25 ≈ ±ATM band). Open deep
    OTM positions need 40–50 so their strike is still on the chain.
    """
    symbol = normalize_symbol(symbol)
    strike_count = max(10, min(int(strike_count or 25), 80))

    # INDstocks first (official live data) — still never places orders
    try:
        from marketdata import indstocks as ind

        if ind.data_enabled() and max_age_sec <= 90:
            live = ind.fetch_option_chain(symbol, expiry, strike_count=strike_count)
            try:
                save_snapshot(live)
            except Exception:  # noqa: BLE001
                pass
            return live
        if ind.data_enabled():
            snap = load_latest_snapshot(symbol, expiry)
            # Only reuse narrow cache when we don't need a wider strike window
            if (
                strike_count <= 25
                and snap
                and snap.get("source") == "LIVE_INDSTOCKS"
                and snap.get("as_of")
            ):
                try:
                    ts = datetime.fromisoformat(snap["as_of"])
                    age = (datetime.now(IST) - ts.astimezone(IST)).total_seconds()
                    if age <= max_age_sec:
                        snap["from_cache"] = True
                        return snap
                except Exception:  # noqa: BLE001
                    pass
            live = ind.fetch_option_chain(symbol, expiry, strike_count=strike_count)
            try:
                save_snapshot(live)
            except Exception:  # noqa: BLE001
                pass
            return live
    except Exception as exc:  # noqa: BLE001
        logger.warning("INDstocks chain fallback to NSE: %s", exc)

    snap = load_latest_snapshot(symbol, expiry)
    if snap and snap.get("as_of"):
        try:
            ts = datetime.fromisoformat(snap["as_of"])
            age = (datetime.now(IST) - ts.astimezone(IST)).total_seconds()
            if age <= max_age_sec and (expiry is None or snap.get("expiry") == expiry):
                snap["from_cache"] = True
                return snap
        except Exception:  # noqa: BLE001
            pass
    live = fetch_option_chain(symbol, expiry)
    live["from_cache"] = False
    return live
