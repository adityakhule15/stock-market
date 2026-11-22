from __future__ import annotations

from pathlib import Path

import pandas as pd
from django.conf import settings


def _folder(interval: str) -> Path:
    folder = Path(settings.DATA_DIR) / "parquet" / interval
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def parquet_path(symbol: str, interval: str) -> Path:
    safe = symbol.replace("/", "_")
    return _folder(interval) / f"{safe}.parquet"


def csv_path(symbol: str, interval: str) -> Path:
    safe = symbol.replace("/", "_")
    return _folder(interval) / f"{safe}.csv"


def _parquet_usable() -> bool:
    try:
        import pyarrow  # noqa: F401

        return True
    except Exception:
        return False


def save_bars(symbol: str, interval: str, df: pd.DataFrame) -> Path:
    out = df.copy()
    if out.index.name != "ts":
        out.index.name = "ts"
    # Prefer CSV when pyarrow DLLs are blocked by Windows Application Control
    if _parquet_usable():
        pq = parquet_path(symbol, interval)
        try:
            out.to_parquet(pq)
            return pq
        except Exception:
            pass
    path = csv_path(symbol, interval)
    out.to_csv(path)
    return path


def load_bars(symbol: str, interval: str) -> pd.DataFrame | None:
    pq = parquet_path(symbol, interval)
    csv = csv_path(symbol, interval)
    df = None
    if pq.exists():
        try:
            df = pd.read_parquet(pq)
        except Exception:
            df = None
    if df is None and csv.exists():
        df = pd.read_csv(csv, index_col=0, parse_dates=True)
    if df is None:
        return None
    if "ts" in df.columns:
        df = df.set_index("ts")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    return df
