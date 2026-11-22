from marketdata.providers.nse_eod import NseEodProvider
from marketdata.providers.yfinance_provider import YFinanceProvider

PROVIDERS = {
    "yfinance": YFinanceProvider,
    "nse_eod": NseEodProvider,
}


def get_provider(name: str):
    cls = PROVIDERS.get(name)
    if cls is None:
        raise KeyError(f"Unknown provider: {name}")
    return cls()
