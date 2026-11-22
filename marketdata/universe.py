from __future__ import annotations

from datetime import date, timedelta

from django.db.models import QuerySet

from config import trading_config as cfg
from marketdata.models import Instrument

# Seed Nifty-ish liquid names for ₹2,000 capital (subset; expand via refresh job).
DEFAULT_UNIVERSE = [
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "BHARTIARTL.NS",
    "ITC.NS",
    "LT.NS",
    "AXISBANK.NS",
    "KOTAKBANK.NS",
    "TATASTEEL.NS",
    "TMPV.NS",  # Tata Motors (ex-TATAMOTORS.NS)
    "HINDUNILVR.NS",
    "SUNPHARMA.NS",
    "MARUTI.NS",
    "NTPC.NS",
    "POWERGRID.NS",
    "ONGC.NS",
    "COALINDIA.NS",
    "WIPRO.NS",
    "HCLTECH.NS",
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "BAJFINANCE.NS",
    "BAJAJFINSV.NS",
    "TECHM.NS",
    "ULTRACEMCO.NS",
    "ASIANPAINT.NS",
    "TITAN.NS",
    "JSWSTEEL.NS",
    "HINDALCO.NS",
    "INDUSINDBK.NS",
    "M&M.NS",
    "NESTLEIND.NS",
    "CIPLA.NS",
    "DRREDDY.NS",
    "BPCL.NS",
    "HEROMOTOCO.NS",
    "EICHERMOT.NS",
    "GRASIM.NS",
    "DIVISLAB.NS",
    "BRITANNIA.NS",
    "APOLLOHOSP.NS",
    "BAJAJ-AUTO.NS",
    "TATACONSUM.NS",
    "SBILIFE.NS",
    "HDFCLIFE.NS",
    "PIDILITIND.NS",
    "DLF.NS",
]


def ensure_seed_instruments() -> int:
    created = 0
    for sym in DEFAULT_UNIVERSE:
        _, was = Instrument.objects.get_or_create(symbol=sym, defaults={"name": sym.replace(".NS", "")})
        if was:
            created += 1
    return created


def tradable_universe(as_of: date | None = None) -> QuerySet[Instrument]:
    as_of = as_of or date.today()
    qs = Instrument.objects.filter(is_active=True, in_asm_gsm=False)
    # Price band when known
    qs = qs.filter(last_price__isnull=False, last_price__gte=cfg.MIN_PRICE, last_price__lte=cfg.MAX_PRICE)
    # Liquidity when known (₹ cr). Zero means not yet computed — allow for bootstrap.
    liquid = qs.filter(avg_traded_value_20d__gte=cfg.MIN_ADV_CR * 1e7) | qs.filter(avg_traded_value_20d=0)
    # Earnings window ±1 session
    window_start = as_of - timedelta(days=1)
    window_end = as_of + timedelta(days=1)
    liquid = liquid.exclude(next_result_date__gte=window_start, next_result_date__lte=window_end)
    symbols = list(liquid.values_list("symbol", flat=True))
    if not symbols:
        ensure_seed_instruments()
        return Instrument.objects.filter(symbol__in=DEFAULT_UNIVERSE, is_active=True)
    return liquid


def shortlist_symbols(limit: int = 40) -> list[str]:
    return list(tradable_universe().order_by("-avg_traded_value_20d", "symbol")[:limit].values_list("symbol", flat=True))
