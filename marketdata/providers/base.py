from __future__ import annotations

from typing import Protocol

import pandas as pd


class ProviderDown(Exception):
    pass


class DataProvider(Protocol):
    name: str
    delay_min: int

    def history(
        self,
        symbol: str,
        interval: str,
        start=None,
        end=None,
        period=None,
    ) -> pd.DataFrame: ...

    def latest_bar(self, symbol: str) -> dict: ...
