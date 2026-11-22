from __future__ import annotations

import logging
import random
import time
from typing import Iterable

import pandas as pd
import yfinance as yf

from marketdata.providers.base import ProviderDown

logger = logging.getLogger(__name__)


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = df.copy()
    # Newer yfinance often returns MultiIndex columns (Price, Ticker)
    if isinstance(out.columns, pd.MultiIndex):
        # Prefer first level names like Open/High/Low/Close/Volume
        level0 = out.columns.get_level_values(0)
        if "Close" in level0 or "close" in [str(x).lower() for x in level0]:
            out.columns = [str(c).lower() for c in level0]
        else:
            out.columns = ["_".join(str(x) for x in tup).lower() for tup in out.columns]
    else:
        out.columns = [str(c).lower() for c in out.columns]
    rename = {
        "adj close": "adj_close",
        "adjclose": "adj_close",
    }
    out = out.rename(columns=rename)
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in out.columns]
    if not keep and any("close" in c for c in out.columns):
        # Flattened multi-ticker leftovers
        mapping = {}
        for c in out.columns:
            cl = c.lower()
            for k in ("open", "high", "low", "close", "volume"):
                if cl == k or cl.startswith(k + "_") or cl.endswith("_" + k):
                    mapping[c] = k
        out = out.rename(columns=mapping)
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in out.columns]
    out = out[keep].dropna(how="all")
    if out.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    else:
        out.index = out.index.tz_convert("Asia/Kolkata")
    out.index.name = "ts"
    return out.astype(float, errors="ignore")


class YFinanceProvider:
    name = "yfinance"
    delay_min = 15

    def history(
        self,
        symbol: str,
        interval: str = "1d",
        start=None,
        end=None,
        period=None,
    ) -> pd.DataFrame:
        for attempt in range(4):
            try:
                ticker = yf.Ticker(symbol)
                kwargs = {"interval": interval, "auto_adjust": False}
                if period:
                    kwargs["period"] = period
                else:
                    if start is not None:
                        kwargs["start"] = start
                    if end is not None:
                        kwargs["end"] = end
                    if "start" not in kwargs and "period" not in kwargs:
                        kwargs["period"] = "1y"
                raw = ticker.history(**kwargs)
                df = _normalize_ohlcv(raw)
                if not df.empty:
                    return df
                # Fallback to download API
                raw2 = yf.download(
                    symbol,
                    interval=interval,
                    period=period or "1y",
                    start=start,
                    end=end,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
                df2 = _normalize_ohlcv(raw2)
                if not df2.empty:
                    return df2
            except Exception as exc:  # noqa: BLE001
                logger.warning("yfinance history attempt %s failed: %s", attempt + 1, exc)
            time.sleep(2**attempt + random.random())
        raise ProviderDown(self.name)

    def history_many(
        self,
        symbols: Iterable[str],
        interval: str = "5m",
        period: str = "5d",
    ) -> dict[str, pd.DataFrame]:
        symbols = list(symbols)
        if not symbols:
            return {}
        result: dict[str, pd.DataFrame] = {}
        # Per-symbol is more reliable under rate limits than a giant batch
        for sym in symbols:
            try:
                result[sym] = self.history(sym, interval=interval, period=period)
            except ProviderDown:
                continue
        if not result:
            raise ProviderDown(self.name)
        return result

    def latest_bar(self, symbol: str) -> dict:
        df = self.history(symbol, interval="5m", period="5d")
        if df.empty:
            df = self.history(symbol, interval="1d", period="5d")
        if df.empty:
            raise ProviderDown(self.name)
        row = df.iloc[-1]
        return {
            "ts": df.index[-1].to_pydatetime(),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": float(row.get("volume", 0.0) or 0.0),
        }
