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
    # Extra liquid mid/large caps often affordable on a ₹2k wallet
    "PNB.NS",
    "BANKBARODA.NS",
    "CANBK.NS",
    "FEDERALBNK.NS",
    "IDFCFIRSTB.NS",
    "YESBANK.NS",
    "NMDC.NS",
    "SAIL.NS",
    "VEDL.NS",
    "TATAPOWER.NS",
    "ADANIGREEN.NS",
    "IRCTC.NS",
    "ZOMATO.NS",
    "PAYTM.NS",
    "NYKAA.NS",
    "POLICYBZR.NS",
]


def affordable_symbols(equity: float, limit: int = 40) -> list[str]:
    """Symbols priced at or below equity × MAX_POSITION_PCT (can buy ≥1 share)."""
    from config import trading_config as cfg

    cap = float(equity) * float(cfg.MAX_POSITION_PCT)
    qs = tradable_universe().filter(last_price__gt=0, last_price__lte=cap)
    syms = list(qs.order_by("-avg_traded_value_20d", "symbol")[:limit].values_list("symbol", flat=True))
    if len(syms) >= 5:
        return syms
    # Fallback: filter DEFAULT by known last_price or include all seed names under MAX_PRICE
    return shortlist_symbols(limit)


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
