from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line - sig


def stochastic(df: pd.DataFrame, period: int = 14) -> pd.Series:
    low = df["low"].rolling(period).min()
    high = df["high"].rolling(period).max()
    return 100 * (df["close"] - low) / (high - low).replace(0, np.nan)


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    return pct_b, bandwidth


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / tr.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.rolling(period).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    out["ret_1"] = c.pct_change(1)
    out["ret_3"] = c.pct_change(3)
    out["ret_5"] = c.pct_change(5)
    out["ret_10"] = c.pct_change(10)
    out["ret_20"] = c.pct_change(20)
    out["gap_pct"] = out["open"] / c.shift(1) - 1
    out["overnight_ret"] = out["open"] / c.shift(1) - 1
    out["intraday_ret"] = c / out["open"] - 1

    for span in (9, 20, 50, 200):
        e = ema(c, span)
        out[f"ema{span}"] = e
        out[f"ema{span}_dist"] = c / e - 1
    out["ema20_slope"] = out["ema20"].pct_change(5)
    out["adx"] = adx(out)
    out["rsi14"] = rsi(c)
    out["macd_hist"] = macd_hist(c)
    out["stoch"] = stochastic(out)
    out["roc10"] = c.pct_change(10)
    out["atr14"] = atr(out)
    out["atr_pct"] = out["atr14"] / c
    pct_b, bw = bollinger(c)
    out["bb_pctb"] = pct_b
    out["bb_bw"] = bw
    out["rvol20"] = c.pct_change().rolling(20).std() * np.sqrt(252)
    out["vol_z"] = (out["volume"] - out["volume"].rolling(20).mean()) / out["volume"].rolling(20).std()
    out["obv"] = obv(out)
    out["obv_slope"] = out["obv"].pct_change(5)
    out["dist_20d_high"] = c / out["high"].rolling(20).max() - 1
    out["dist_20d_low"] = c / out["low"].rolling(20).min() - 1
    out["dist_52w_high"] = c / out["high"].rolling(252, min_periods=50).max() - 1
    out["inside_day"] = ((out["high"] < out["high"].shift(1)) & (out["low"] > out["low"].shift(1))).astype(float)
    rng = out["high"] - out["low"]
    out["nr7"] = (rng == rng.rolling(7).min()).astype(float)
    out["dow"] = out.index.day_of_week.astype(float)
    return out
