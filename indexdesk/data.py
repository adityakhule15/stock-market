"""Backfill + session append for NIFTY / BANKNIFTY bars; live spot via INDstocks."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from indexdesk.constants import (
    DAILY_YEARS,
    INTRADAY_1M_PERIOD,
    INTRADAY_5M_PERIOD,
    UNDERLYINGS,
    YF_MAP,
)
from marketdata.models import Instrument
from marketdata.service import MarketDataService

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def ensure_index_instruments() -> list[str]:
    """Create Instrument rows for Yahoo index symbols."""
    out = []
    for name, yf_sym in YF_MAP.items():
        inst, _ = Instrument.objects.get_or_create(
            symbol=yf_sym,
            defaults={"name": name, "exchange": "NSE", "sector": "INDEX"},
        )
        if not inst.name or inst.name == yf_sym:
            inst.name = name
            inst.sector = "INDEX"
            inst.save(update_fields=["name", "sector", "updated_at"])
        out.append(yf_sym)
    return out


def backfill_index_history(*, years: int = DAILY_YEARS, force: bool = False) -> dict[str, Any]:
    """5y daily + rolling 5m/1m for NIFTY and BANKNIFTY."""
    ensure_index_instruments()
    svc = MarketDataService()
    detail: dict[str, Any] = {}
    for name, yf_sym in YF_MAP.items():
        row: dict[str, int | str] = {}
        try:
            n_d = svc.backfill_symbol(yf_sym, years=years, interval="1d")
            row["1d"] = int(n_d)
        except Exception as exc:  # noqa: BLE001
            logger.warning("index daily backfill %s: %s", yf_sym, exc)
            row["1d"] = 0
            row["1d_err"] = str(exc)[:120]
        for interval, period in (("5m", INTRADAY_5M_PERIOD), ("1m", INTRADAY_1M_PERIOD)):
            try:
                df, source = svc.history(yf_sym, interval=interval, period=period, use_cache=not force)
                n = svc.upsert_bars(yf_sym, interval, df, source)
                row[interval] = int(n)
            except Exception as exc:  # noqa: BLE001
                logger.warning("index %s backfill %s: %s", interval, yf_sym, exc)
                row[interval] = 0
                row[f"{interval}_err"] = str(exc)[:120]
        detail[name] = row
    return detail


def append_session_bars(underlyings: tuple[str, ...] | list[str] | None = None) -> dict[str, Any]:
    """After/during session: refresh 1m + 5m from Yahoo and upsert into DB."""
    names = list(underlyings or UNDERLYINGS)
    svc = MarketDataService()
    out: dict[str, Any] = {}
    for name in names:
        yf_sym = YF_MAP.get(name.upper())
        if not yf_sym:
            continue
        row: dict[str, int | str] = {}
        for interval, period in (("5m", "5d"), ("1m", "1d")):
            try:
                df, source = svc.history(yf_sym, interval=interval, period=period, use_cache=False)
                row[interval] = int(svc.upsert_bars(yf_sym, interval, df, source))
            except Exception as exc:  # noqa: BLE001
                logger.debug("append %s %s: %s", name, interval, exc)
                row[interval] = 0
                row[f"{interval}_err"] = str(exc)[:100]
        # Also refresh today's daily bar once available
        try:
            df_d, src_d = svc.history(yf_sym, interval="1d", period="5d", use_cache=False)
            row["1d"] = int(svc.upsert_bars(yf_sym, "1d", df_d, src_d))
        except Exception as exc:  # noqa: BLE001
            row["1d"] = 0
            row["1d_err"] = str(exc)[:100]
        out[name] = row
    return out


def live_spot(underlying: str) -> dict[str, Any]:
    """Prefer INDstocks option-chain spot; fall back to Yahoo/DB last close."""
    code = underlying.upper().replace(".NS", "")
    # 1) INDstocks / NSE live chain spot
    try:
        from marketdata.option_chain import get_chain_cached_or_live

        chain = get_chain_cached_or_live(code, expiry=None, max_age_sec=45)
        spot = float(chain.get("spot") or 0)
        if spot > 0:
            return {
                "underlying": code,
                "spot": spot,
                "source": chain.get("source") or "LIVE_INDSTOCKS",
                "as_of": chain.get("as_of_label") or "",
                "from_cache": bool(chain.get("from_cache")),
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("live_spot chain %s: %s", code, exc)

    # 2) INDstocks LTP via index scrip
    try:
        from marketdata import indstocks

        _seg, scrip = indstocks.underlying_scrip(code)
        if scrip:
            quotes = indstocks.get_ltp([scrip])
            payload = quotes if isinstance(quotes, dict) else {}
            data = payload.get("data") or payload.get("result") or payload
            ltp = 0.0
            if isinstance(data, dict):
                row = data.get(scrip) or data.get(str(scrip)) or next(iter(data.values()), None)
                if isinstance(row, dict):
                    ltp = float(row.get("ltp") or row.get("last_price") or row.get("price") or 0)
                elif isinstance(row, (int, float)):
                    ltp = float(row)
            if ltp > 0:
                return {
                    "underlying": code,
                    "spot": ltp,
                    "source": "LIVE_INDSTOCKS_LTP",
                    "as_of": "",
                    "from_cache": False,
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("live_spot indstocks ltp %s: %s", code, exc)

    # 3) Yahoo / DB bars
    yf_sym = YF_MAP.get(code)
    if yf_sym:
        svc = MarketDataService()
        try:
            bar, source = svc.latest_bar(yf_sym)
            return {
                "underlying": code,
                "spot": float(bar.get("close") or 0),
                "source": source or "yfinance",
                "as_of": bar.get("ts").isoformat() if bar.get("ts") else "",
                "from_cache": False,
            }
        except Exception:  # noqa: BLE001
            pass
        try:
            df = svc.bars_df(yf_sym, interval="1m", limit=5)
            if df is not None and not df.empty:
                return {
                    "underlying": code,
                    "spot": float(df["close"].iloc[-1]),
                    "source": "db_1m",
                    "as_of": str(df.index[-1]),
                    "from_cache": True,
                }
        except Exception:  # noqa: BLE001
            pass
        try:
            df = svc.bars_df(yf_sym, interval="1d", limit=5)
            if df is not None and not df.empty:
                return {
                    "underlying": code,
                    "spot": float(df["close"].iloc[-1]),
                    "source": "db_1d",
                    "as_of": str(df.index[-1]),
                    "from_cache": True,
                }
        except Exception:  # noqa: BLE001
            pass

    return {"underlying": code, "spot": 0.0, "source": "none", "as_of": "", "from_cache": False}


def poll_index_session() -> dict[str, Any]:
    """Session job: refresh intraday bars + touch last_price from live spot."""
    appended = append_session_bars()
    spots = {}
    for name in UNDERLYINGS:
        spot = live_spot(name)
        spots[name] = spot
        yf_sym = YF_MAP[name]
        if spot["spot"] > 0:
            try:
                inst = Instrument.objects.get(symbol=yf_sym)
                inst.last_price = float(spot["spot"])
                inst.save(update_fields=["last_price", "updated_at"])
            except Instrument.DoesNotExist:
                pass
    return {
        "appended": appended,
        "spots": {k: {"spot": v["spot"], "source": v["source"]} for k, v in spots.items()},
        "ts": timezone.now().astimezone(IST).isoformat(),
    }


def eod_append_and_summary() -> dict[str, Any]:
    """After close: persist full day 1m/5m + daily, for overnight retrain."""
    detail = append_session_bars()
    # Prefer longer 1m window once at EOD
    svc = MarketDataService()
    for name, yf_sym in YF_MAP.items():
        try:
            df, source = svc.history(yf_sym, interval="1m", period=INTRADAY_1M_PERIOD, use_cache=False)
            detail.setdefault(name, {})["1m_eod"] = int(svc.upsert_bars(yf_sym, "1m", df, source))
        except Exception as exc:  # noqa: BLE001
            detail.setdefault(name, {})["1m_eod_err"] = str(exc)[:100]
        try:
            df5, src5 = svc.history(yf_sym, interval="5m", period=INTRADAY_5M_PERIOD, use_cache=False)
            detail.setdefault(name, {})["5m_eod"] = int(svc.upsert_bars(yf_sym, "5m", df5, src5))
        except Exception as exc:  # noqa: BLE001
            detail.setdefault(name, {})["5m_eod_err"] = str(exc)[:100]
    return {"ok": True, "detail": detail, "as_of": datetime.now(IST).isoformat()}
