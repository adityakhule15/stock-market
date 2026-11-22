from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import pandas as pd
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from marketdata.cache import load_bars, save_bars
from marketdata.models import Bar, Instrument
from marketdata.providers import get_provider
from marketdata.providers.base import ProviderDown

logger = logging.getLogger(__name__)


class MarketDataService:
    def __init__(self, provider_names: list[str] | None = None):
        names = provider_names or list(settings.DATA_PROVIDERS)
        self.providers = []
        for name in names:
            try:
                self.providers.append(get_provider(name))
            except KeyError:
                logger.warning("Skipping unknown provider %s", name)

    def history(
        self,
        symbol: str,
        interval: str = "1d",
        start=None,
        end=None,
        period=None,
        use_cache: bool = True,
    ) -> tuple[pd.DataFrame, str]:
        if use_cache:
            cached = load_bars(symbol, interval)
            if cached is not None and not cached.empty:
                # If period/start requested and cache covers enough, return it
                if period is None and start is None:
                    return cached, "cache"
        last_err: Exception | None = None
        for provider in self.providers:
            if interval not in ("1d", "d", "day") and provider.name == "nse_eod":
                continue
            # Skip slow EOD archive for multi-year / long period history requests
            if provider.name == "nse_eod" and period and any(period.endswith(s) for s in ("y", "mo")):
                if period.endswith("y") and int(period[:-1] or "1") >= 1:
                    continue
                if period.endswith("mo") and int(period[:-2] or "1") > 1:
                    continue
            try:
                df = provider.history(symbol, interval=interval, start=start, end=end, period=period)
                if df is not None and not df.empty:
                    save_bars(symbol, interval, df)
                    return df, provider.name
            except ProviderDown as exc:
                last_err = exc
                logger.info("Provider %s down for %s", provider.name, symbol)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("Provider %s error: %s", provider.name, exc)
        raise ProviderDown(str(last_err) if last_err else "all providers down")

    def latest_bar(self, symbol: str) -> tuple[dict, str]:
        last_err: Exception | None = None
        for provider in self.providers:
            try:
                bar = provider.latest_bar(symbol)
                bar["source"] = provider.name
                return bar, provider.name
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        raise ProviderDown(str(last_err) if last_err else "all providers down")

    def upsert_bars(
        self,
        symbol: str,
        interval: str,
        df: pd.DataFrame,
        source: str,
    ) -> int:
        inst, _ = Instrument.objects.get_or_create(symbol=symbol, defaults={"name": symbol})
        if df.empty:
            return 0
        rows = []
        for ts, row in df.iterrows():
            ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if timezone.is_naive(ts_dt):
                ts_dt = timezone.make_aware(ts_dt, timezone.get_current_timezone())
            rows.append(
                Bar(
                    instrument=inst,
                    interval=interval,
                    ts=ts_dt,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0) or 0),
                    source=source,
                )
            )
        with transaction.atomic():
            # SQLite-friendly upsert via ignore conflicts then update is heavy; delete+create for range
            if rows:
                Bar.objects.filter(
                    instrument=inst,
                    interval=interval,
                    ts__in=[r.ts for r in rows],
                ).delete()
                Bar.objects.bulk_create(rows, batch_size=500)
        if rows:
            last = rows[-1]
            inst.last_price = last.close
            inst.save(update_fields=["last_price", "updated_at"])
        return len(rows)

    def backfill_symbol(self, symbol: str, years: int = 5, interval: str = "1d") -> int:
        period = f"{years}y" if interval in ("1d", "d", "day") else "60d"
        df, source = self.history(symbol, interval=interval, period=period, use_cache=False)
        return self.upsert_bars(symbol, interval if interval != "d" else "1d", df, source)

    def backfill_many(self, symbols: Iterable[str], years: int = 5) -> dict[str, int]:
        out = {}
        for sym in symbols:
            try:
                out[sym] = self.backfill_symbol(sym, years=years)
            except Exception as exc:  # noqa: BLE001
                logger.error("Backfill failed %s: %s", sym, exc)
                out[sym] = 0
        return out

    def bars_df(self, symbol: str, interval: str = "1d", limit: int | None = None) -> pd.DataFrame:
        try:
            inst = Instrument.objects.get(symbol=symbol)
        except Instrument.DoesNotExist:
            df, _ = self.history(symbol, interval=interval, period="2y")
            return df
        qs = Bar.objects.filter(instrument=inst, interval=interval).order_by("ts")
        if limit:
            qs = qs.order_by("-ts")[:limit]
            rows = list(reversed(list(qs)))
        else:
            rows = list(qs)
        if not rows:
            df, source = self.history(symbol, interval=interval, period="2y")
            self.upsert_bars(symbol, interval, df, source)
            return df
        data = {
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        }
        idx = pd.DatetimeIndex([r.ts for r in rows], name="ts")
        return pd.DataFrame(data, index=idx)
