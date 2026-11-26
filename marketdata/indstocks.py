"""INDstocks market-data client — DATA ONLY.

Hard rule: never places, modifies, or cancels broker orders.
Use INDSTOCKS_ACCESS_TOKEN in .env for live LTP / option chain.

Tokens expire ~24h (INDstocks dashboard). On 403/expired we cool down and fall
back to NSE so logs are not spammed every poll.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
BASE = "https://api.indstocks.com"
TOKEN_HELP = "https://www.indstocks.com/app/api-trading/access-tokens"

_BLOCKED_PATH_PREFIXES = (
    "/order",
    "/orders",
    "/smart",
    "/trade",
    "/basket",
    "/gtt",
    "/margin",
)

# After auth failure, skip INDstocks until cooldown or token string changes
_auth_fail_until = 0.0
_auth_fail_token = ""
_AUTH_COOLDOWN_SEC = 900.0
_auth_warned = False

# HTTP 429 — pause IndStocks network calls; serve memory/DB cache / NSE
_rate_limit_until = 0.0
_RATE_LIMIT_COOLDOWN_SEC = 90.0
_rate_limit_warned = False

# Short in-process caches so Positions polling + scans don't stampede the API
_chain_mem: dict[str, tuple[float, dict[str, Any]]] = {}
_CHAIN_MEM_TTL_SEC = 20.0
_expiries_mem: dict[str, tuple[float, list[str]]] = {}
_EXPIRIES_MEM_TTL_SEC = 300.0

# Seed IDs from INDstocks docs (refreshed via instruments when possible)
_INDEX_SCRIP = {
    "NIFTY": "40000001",  # NIFTY 50
    "BANKNIFTY": "40000003",  # BANK NIFTY
}

# BSE Sensex options are not on INDstocks NSE index feed — use NSE/Yahoo fallback
_INDSTOCKS_UNSUPPORTED = frozenset({"SENSEX", "BSESN", "^BSESN"})


def supports_underlying(symbol: str) -> bool:
    code = symbol.upper().replace(".NS", "").replace("^", "")
    return code not in _INDSTOCKS_UNSUPPORTED

# Exact index names from /market/instruments?source=index (col 2)
_INDEX_NAME_MAP = {
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "BANK NIFTY": "BANKNIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "BANKNIFTY": "BANKNIFTY",
}

_scrip_cache: dict[str, str] = {}
_scrip_cache_ts = 0.0
_SCRIP_TTL = 86_400.0
# Bump to invalidate bad cache entries (e.g. NIFTY 500 mistaken for NIFTY 50)
_SCRIP_CACHE_VERSION = 2
_scrip_cache_ver = 0


class BrokerOrdersDisabled(RuntimeError):
    """Raised if any code path tries to hit an order endpoint."""


def _normalize_token(raw: str) -> str:
    tok = (raw or "").strip().strip('"').strip("'")
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    return tok


def _db_token() -> str:
    try:
        from portfolio.models import PortfolioSettings

        ps = PortfolioSettings.get_solo()
        return _normalize_token(ps.indstocks_access_token or "")
    except Exception:  # noqa: BLE001
        return ""


def _env_token() -> str:
    return _normalize_token(os.getenv("INDSTOCKS_ACCESS_TOKEN") or "")


def access_token() -> str:
    """Return a non-expired token. Prefer DB (Settings / popup), then .env.

    Expired leftovers in .env must not count as a usable token (and must not
    keep the UI popup stuck as if a valid token were present).
    """
    for tok in (_db_token(), _env_token()):
        if tok and not token_expired(tok):
            return tok
    return ""


def _jwt_exp_unix(token: str) -> float | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        pad = "=" * ((4 - len(parts[1]) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001
        return None


def clear_expired_db_token() -> bool:
    """Delete stored DB token when JWT exp has passed. Returns True if cleared."""
    try:
        from django.utils import timezone as dj_tz

        from portfolio.models import PortfolioSettings

        ps = PortfolioSettings.get_solo()
        tok = _normalize_token(ps.indstocks_access_token or "")
        if not tok:
            return False
        if not token_expired(tok):
            return False
        ps.indstocks_access_token = ""
        ps.indstocks_token_exp = None
        ps.save(update_fields=["indstocks_access_token", "indstocks_token_exp", "updated_at"])
        logger.info("Cleared expired INDstocks DB token (regenerate at %s)", TOKEN_HELP)
        return True
    except Exception:  # noqa: BLE001
        return False


def save_access_token(raw: str) -> dict[str, Any]:
    """Persist JWT in PortfolioSettings; decode exp. Clears auth cooldown."""
    from datetime import datetime, timezone as dt_tz

    from django.utils import timezone as dj_tz

    from portfolio.models import PortfolioSettings

    tok = _normalize_token(raw)
    if not tok:
        raise ValueError("token required")
    exp_unix = _jwt_exp_unix(tok)
    if exp_unix is not None and time.time() >= (exp_unix - 30):
        raise ValueError(f"token already expired — regenerate at {TOKEN_HELP}")
    exp_dt = None
    if exp_unix is not None:
        exp_dt = datetime.fromtimestamp(exp_unix, tz=dt_tz.utc)
        if dj_tz.is_naive(exp_dt):
            exp_dt = dj_tz.make_aware(exp_dt, timezone=dt_tz.utc)
    ps = PortfolioSettings.get_solo()
    ps.indstocks_access_token = tok
    ps.indstocks_token_exp = exp_dt
    ps.save(update_fields=["indstocks_access_token", "indstocks_token_exp", "updated_at"])
    global _auth_fail_until, _auth_fail_token, _auth_warned
    _auth_fail_until = 0.0
    _auth_fail_token = ""
    _auth_warned = False
    return {
        "ok": True,
        "has_token": True,
        "expired": False,
        "expires_at": exp_dt.isoformat() if exp_dt else None,
        "help_url": TOKEN_HELP,
    }


def token_status() -> dict[str, Any]:
    """UI/API status for token popup (never returns the raw token).

    Popup should open only when needs_token is True (no valid JWT).
    """
    clear_expired_db_token()
    db = _db_token()
    env = _env_token()
    tok = ""
    source = "none"
    if db and not token_expired(db):
        tok, source = db, "db"
    elif env and not token_expired(env):
        tok, source = env, "env"
    elif db:
        source = "db_expired"
    elif env:
        source = "env_expired"
    exp = _jwt_exp_unix(tok) if tok else None
    # If only an expired leftover exists, still surface its exp for the UI message
    if exp is None:
        leftover = db or env
        if leftover:
            exp = _jwt_exp_unix(leftover)
    exp_iso = None
    if exp is not None:
        exp_iso = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
    has_token = bool(tok)
    return {
        "has_token": has_token,
        "expired": not has_token,
        "needs_token": not has_token,
        "source": source,
        "expires_at": exp_iso,
        "help_url": TOKEN_HELP,
        "data_enabled": data_enabled() if has_token else False,
    }


def token_expired(token: str | None = None) -> bool:
    tok = token if token is not None else access_token()
    if not tok:
        return True
    exp = _jwt_exp_unix(tok)
    if exp is None:
        return False
    # 30s skew
    return time.time() >= (exp - 30)


def _mark_auth_failed(token: str, detail: str) -> None:
    global _auth_fail_until, _auth_fail_token, _auth_warned
    _auth_fail_until = time.time() + _AUTH_COOLDOWN_SEC
    _auth_fail_token = token
    if not _auth_warned:
        logger.error(
            "INDstocks auth failed (%s). Regenerate token at %s and update "
            "INDSTOCKS_ACCESS_TOKEN in .env — cooling down %ss (NSE fallback).",
            detail[:160],
            TOKEN_HELP,
            int(_AUTH_COOLDOWN_SEC),
        )
        _auth_warned = True


def _clear_auth_fail_if_token_changed(token: str) -> None:
    global _auth_fail_until, _auth_fail_token, _auth_warned
    if _auth_fail_token and token and token != _auth_fail_token:
        _auth_fail_until = 0.0
        _auth_fail_token = ""
        _auth_warned = False


def _mark_rate_limited(detail: str = "HTTP 429") -> None:
    global _rate_limit_until, _rate_limit_warned
    _rate_limit_until = time.time() + _RATE_LIMIT_COOLDOWN_SEC
    if not _rate_limit_warned:
        logger.warning(
            "INDstocks rate-limited (%s) — cooling down %ss (use cache/NSE).",
            detail[:120],
            int(_RATE_LIMIT_COOLDOWN_SEC),
        )
        _rate_limit_warned = True


def rate_limited() -> bool:
    return time.time() < _rate_limit_until


def data_enabled() -> bool:
    """True when a usable (non-expired, not in auth/rate-limit cooldown) token is set."""
    clear_expired_db_token()
    tok = access_token()
    if not tok:
        return False
    _clear_auth_fail_if_token_changed(tok)
    if token_expired(tok):
        _mark_auth_failed(tok, "JWT exp claim passed — access token expired (~24h)")
        clear_expired_db_token()
        return False
    if time.time() < _auth_fail_until and tok == _auth_fail_token:
        return False
    if rate_limited():
        return False
    return True


def _assert_data_only(path: str) -> None:
    p = path.lower().split("?")[0]
    if any(p == b or p.startswith(b + "/") for b in _BLOCKED_PATH_PREFIXES):
        raise BrokerOrdersDisabled(
            f"Blocked INDstocks order path {path!r}. StockScope is signals/data only."
        )
    if any(x in p for x in ("/place", "/cancel", "/modify")):
        raise BrokerOrdersDisabled(f"Blocked trading-looking path {path!r}. Data only.")


def _headers() -> dict[str, str]:
    token = access_token()
    if not token:
        raise RuntimeError("INDSTOCKS_ACCESS_TOKEN not set in .env")
    if token_expired(token):
        _mark_auth_failed(token, "JWT exp claim passed — access token expired (~24h)")
        raise RuntimeError(
            f"INDstocks access token expired — regenerate at {TOKEN_HELP}"
        )
    return {"Authorization": token, "Accept": "application/json"}


def _get(path: str, params: dict | None = None, *, raw: bool = False) -> Any:
    _assert_data_only(path)
    if rate_limited():
        raise RuntimeError(
            f"INDstocks cooling down after 429 ({int(max(0, _rate_limit_until - time.time()))}s left)"
        )
    token = access_token()
    r = requests.get(
        f"{BASE}{path}",
        headers=_headers(),
        params=params or {},
        timeout=25,
    )
    if r.status_code == 429:
        _mark_rate_limited(r.text[:120] or "Too Many Requests")
        raise RuntimeError("INDstocks HTTP 429 Too Many Requests")
    if r.status_code in {401, 403}:
        detail = ""
        try:
            body = r.json()
            detail = (
                body.get("message")
                or body.get("error")
                or (body.get("error_type") or "")
                or r.text[:180]
            )
        except Exception:  # noqa: BLE001
            detail = r.text[:180]
        _mark_auth_failed(token, detail or f"HTTP {r.status_code}")
        raise RuntimeError(
            f"INDstocks auth HTTP {r.status_code}: {detail[:160]} — regenerate at {TOKEN_HELP}"
        )
    r.raise_for_status()
    global _rate_limit_warned
    _rate_limit_warned = False
    if raw:
        return r.text
    return r.json() if r.content else {}


def list_expiries(underlying: str) -> list[str]:
    """Upcoming expiries YYYY-MM-DD for an underlying (cached ~5 min)."""
    key = underlying.upper()
    hit = _expiries_mem.get(key)
    if hit and time.time() - hit[0] < _EXPIRIES_MEM_TTL_SEC:
        return list(hit[1])
    data = _get(
        "/market/instruments/expiries",
        {"underlying": key, "segment": "DERIVATIVE"},
    )
    rows = data.get("data") if isinstance(data, dict) else data
    out = [str(x) for x in (rows or [])]
    _expiries_mem[key] = (time.time(), out)
    return list(out)


def _load_underlying_scrips(force: bool = False) -> dict[str, str]:
    global _scrip_cache, _scrip_cache_ts, _scrip_cache_ver
    now = time.time()
    if (
        not force
        and _scrip_cache
        and _scrip_cache_ver == _SCRIP_CACHE_VERSION
        and now - _scrip_cache_ts < _SCRIP_TTL
    ):
        return _scrip_cache
    out: dict[str, str] = dict(_INDEX_SCRIP)
    try:
        text = _get("/market/instruments", {"source": "index"}, raw=True)
        for row in csv.reader(io.StringIO(text)):
            if not row or row[0].upper() == "EXCH":
                continue
            if len(row) < 3:
                continue
            name = (row[1] or "").strip().upper()
            sid = (row[2] or "").strip()
            # Exact match only — "NIFTY 50" must NOT match "NIFTY 500"
            mapped = _INDEX_NAME_MAP.get(name)
            if mapped and sid:
                out[mapped] = sid
    except Exception as exc:  # noqa: BLE001
        logger.warning("INDstocks index instruments: %s", exc)
    try:
        text = _get("/market/instruments", {"source": "equity"}, raw=True)
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            sym = (row.get("SYMBOL_NAME") or row.get("TRADING_SYMBOL") or "").strip().upper()
            exch = (row.get("EXCH") or "").strip().upper()
            sid = (row.get("SECURITY_ID") or "").strip()
            series = (row.get("SERIES") or "").strip().upper()
            if not sym or not sid or exch != "NSE":
                continue
            if series and series not in {"EQ", "BE", ""}:
                continue
            # Prefer first EQ row
            if sym not in out:
                out[sym] = sid
    except Exception as exc:  # noqa: BLE001
        logger.warning("INDstocks equity instruments: %s", exc)
    _scrip_cache, _scrip_cache_ts, _scrip_cache_ver = out, now, _SCRIP_CACHE_VERSION
    return out


def underlying_scrip(symbol: str) -> tuple[str, str]:
    """Return (segment INDEX|EQUITY, underlying-scrip id)."""
    code = symbol.upper().replace(".NS", "").replace("^", "")
    if code in _INDSTOCKS_UNSUPPORTED:
        raise RuntimeError(f"INDstocks unsupported underlying {code}")
    scrips = _load_underlying_scrips()
    if code in {"NIFTY", "BANKNIFTY"}:
        return "INDEX", scrips.get(code) or _INDEX_SCRIP.get(code, "")
    sid = scrips.get(code)
    if not sid:
        raise RuntimeError(f"No INDstocks underlying-scrip for {code}")
    return "EQUITY", sid


def fetch_option_chain(symbol: str, expiry: str | None = None, strike_count: int = 25) -> dict[str, Any]:
    """Live option chain → same row shape as marketdata.option_chain.parse_chain.

    Uses a short in-memory TTL so Positions 5–15s polls + scans share one fetch.
    """
    code = symbol.upper().replace(".NS", "").replace("^", "")
    if code in _INDSTOCKS_UNSUPPORTED:
        raise RuntimeError(f"INDstocks unsupported underlying {code}")
    strike_count = max(10, min(int(strike_count or 25), 80))
    segment, scrip = underlying_scrip(code)
    if not scrip:
        raise RuntimeError(f"Missing underlying-scrip for {code}")

    expiries = list_expiries(code)
    if not expiries:
        raise RuntimeError(f"No INDstocks expiries for {code}")
    if expiry is None:
        expiry = expiries[0]
    # Accept DD-Mon-YYYY from NSE-style callers
    if expiry and "-" in expiry and not expiry[:4].isdigit():
        expiry = datetime.strptime(expiry, "%d-%b-%Y").strftime("%Y-%m-%d")

    # Prefer a cached wider chain when a narrower strike_count is requested
    mem_key = f"{code}|{expiry}|{strike_count}"
    now_m = time.time()
    for k, (ts, cached) in list(_chain_mem.items()):
        if now_m - ts > _CHAIN_MEM_TTL_SEC:
            _chain_mem.pop(k, None)
            continue
        try:
            k_code, k_exp, k_sc = k.split("|", 2)
            if k_code == code and k_exp == str(expiry) and int(k_sc) >= strike_count:
                out = dict(cached)
                out["from_cache"] = True
                return out
        except Exception:  # noqa: BLE001
            continue

    if rate_limited():
        hit = _chain_mem.get(mem_key)
        if hit:
            out = dict(hit[1])
            out["from_cache"] = True
            return out
        raise RuntimeError("INDstocks rate-limited — no cached chain")

    raw = _get(
        "/market/option-chain",
        {
            "exchange": "NSE",
            "segment": segment,
            "underlying-scrip": scrip,
            "expiry": expiry,
            "strike_count": strike_count,
        },
    )
    data = (raw or {}).get("data") or raw or {}
    strikes_map = data.get("strikes") or {}
    spot = float(data.get("underlying_ltp") or 0)
    exp_label = data.get("expiry") or expiry
    try:
        exp_disp = datetime.strptime(exp_label, "%Y-%m-%d").strftime("%d-%b-%Y")
    except ValueError:
        exp_disp = str(exp_label)

    rows: list[dict] = []
    for strike_s, legs in strikes_map.items():
        try:
            strike = float(strike_s)
        except (TypeError, ValueError):
            continue
        ce = (legs or {}).get("ce") or {}
        pe = (legs or {}).get("pe") or {}
        rows.append(
            {
                "strike": strike,
                "ce_ltp": float(ce.get("last_price") or 0),
                "ce_oi": int(ce.get("oi") or 0),
                "ce_vol": int(ce.get("volume") or 0),
                "ce_iv": float(ce.get("iv") or 0),
                "ce_bid": float(ce.get("top_bid_price") or 0),
                "ce_ask": float(ce.get("top_ask_price") or 0),
                "ce_security_id": str(ce.get("security_id") or ""),
                "pe_ltp": float(pe.get("last_price") or 0),
                "pe_oi": int(pe.get("oi") or 0),
                "pe_vol": int(pe.get("volume") or 0),
                "pe_iv": float(pe.get("iv") or 0),
                "pe_bid": float(pe.get("top_bid_price") or 0),
                "pe_ask": float(pe.get("top_ask_price") or 0),
                "pe_security_id": str(pe.get("security_id") or ""),
            }
        )
    rows.sort(key=lambda x: x["strike"])
    now = datetime.now(IST)
    out = {
        "symbol": code,
        "expiry": exp_disp,
        "expiry_iso": exp_label,
        "spot": spot,
        "as_of": now.isoformat(),
        "as_of_label": now.strftime("%d-%b-%Y %H:%M IST"),
        "source": "LIVE_INDSTOCKS",
        "n_strikes": len(rows),
        "rows": rows,
        "expiries": expiries,
        "from_cache": False,
    }
    _chain_mem[mem_key] = (time.time(), out)
    return out


def get_ltp(scrip_codes: list[str]) -> dict[str, Any]:
    return _get("/market/quotes/ltp", {"scrip-codes": ",".join(scrip_codes)})


def get_full_quotes(scrip_codes: list[str]) -> dict[str, Any]:
    return _get("/market/quotes/full", {"scrip-codes": ",".join(scrip_codes)})


def ping() -> dict[str, Any]:
    """Lightweight auth check — funds is read-only account data, not an order."""
    # /funds is portfolio read; block list doesn't include it. Prefer expiries for NIFTY.
    ex = list_expiries("NIFTY")
    return {"ok": True, "nifty_expiries": ex[:3], "token_set": True}


def place_order(*_a, **_k):  # noqa: ANN001
    raise BrokerOrdersDisabled("place_order is disabled — StockScope never sends broker orders.")


def modify_order(*_a, **_k):  # noqa: ANN001
    raise BrokerOrdersDisabled("modify_order is disabled.")


def cancel_order(*_a, **_k):  # noqa: ANN001
    raise BrokerOrdersDisabled("cancel_order is disabled.")
