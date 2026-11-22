from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from marketdata.providers.base import ProviderDown

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class NseEodProvider:
    """NSE published end-of-day bhavcopy / archives (best-effort, free, no login)."""

    name = "nse_eod"
    delay_min = 0  # EOD only; not intraday live

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; StockScope/1.0)",
        "Accept": "*/*",
    }

    def history(
        self,
        symbol: str,
        interval: str = "1d",
        start=None,
        end=None,
        period=None,
    ) -> pd.DataFrame:
        if interval not in ("1d", "d", "day"):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        ticker = symbol.replace(".NS", "").upper()
        end_d = _as_date(end) or date.today()
        if start:
            start_d = _as_date(start)
        elif period:
            start_d = end_d - _period_to_delta(period)
        else:
            start_d = end_d - timedelta(days=10)
        # Day-by-day archive pulls are slow; cap fallback lookbacks.
        if (end_d - start_d).days > 14:
            start_d = end_d - timedelta(days=14)

        frames: list[pd.DataFrame] = []
        d = start_d
        while d <= end_d:
            if d.weekday() < 5:
                try:
                    day_df = self._fetch_bhavcopy(d)
                    if day_df is not None and not day_df.empty:
                        row = day_df[day_df["SYMBOL"] == ticker]
                        if not row.empty:
                            r = row.iloc[0]
                            frames.append(
                                pd.DataFrame(
                                    {
                                        "open": [float(r["OPEN"])],
                                        "high": [float(r["HIGH"])],
                                        "low": [float(r["LOW"])],
                                        "close": [float(r["CLOSE"])],
                                        "volume": [float(r.get("TOTTRDQTY", 0) or 0)],
                                    },
                                    index=pd.DatetimeIndex(
                                        [datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST)],
                                        name="ts",
                                    ),
                                )
                            )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("bhavcopy %s failed: %s", d, exc)
            d += timedelta(days=1)
        if not frames:
            raise ProviderDown(self.name)
        return pd.concat(frames).sort_index()

    def latest_bar(self, symbol: str) -> dict:
        # Walk back a few sessions for the latest available EOD
        for i in range(7):
            d = date.today() - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            try:
                day_df = self._fetch_bhavcopy(d)
            except Exception:  # noqa: BLE001
                continue
            if day_df is None or day_df.empty:
                continue
            ticker = symbol.replace(".NS", "").upper()
            row = day_df[day_df["SYMBOL"] == ticker]
            if row.empty:
                continue
            r = row.iloc[0]
            return {
                "ts": datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST),
                "o": float(r["OPEN"]),
                "h": float(r["HIGH"]),
                "l": float(r["LOW"]),
                "c": float(r["CLOSE"]),
                "v": float(r.get("TOTTRDQTY", 0) or 0),
            }
        raise ProviderDown(self.name)

    def _fetch_bhavcopy(self, d: date) -> pd.DataFrame | None:
        # Archives endpoint used by many free tools; may change — service falls back.
        # Format: https://archives.nseindia.com/content/historical/EQUITIES/YYYY/MMM/cmDDMONYYYYbhav.csv.zip
        mon = d.strftime("%b").upper()
        url = (
            f"https://archives.nseindia.com/content/historical/EQUITIES/"
            f"{d.year}/{mon}/cm{d.strftime('%d')}{mon}{d.year}bhav.csv.zip"
        )
        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=20)
            if resp.status_code != 200:
                return None
            df = pd.read_csv(io.BytesIO(resp.content), compression="zip")
            df.columns = [c.strip().upper() for c in df.columns]
            # Prefer EQ series
            if "SERIES" in df.columns:
                df = df[df["SERIES"] == "EQ"]
            return df
        except Exception as exc:  # noqa: BLE001
            logger.debug("fetch bhavcopy failed %s: %s", url, exc)
            return None


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return pd.Timestamp(v).date()


def _period_to_delta(period: str) -> timedelta:
    period = period.lower().strip()
    if period.endswith("d"):
        return timedelta(days=int(period[:-1]))
    if period.endswith("mo"):
        return timedelta(days=30 * int(period[:-2]))
    if period.endswith("y"):
        return timedelta(days=365 * int(period[:-1]))
    return timedelta(days=30)
