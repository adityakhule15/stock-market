"""Shared constants for the Patterns Desk."""

from __future__ import annotations

# Yahoo Finance symbols for index cash history
YF_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}

UNDERLYINGS = ("NIFTY", "BANKNIFTY")

MODEL_NAME = "index_pattern_lgbm"

# History depths (free-source limits)
DAILY_YEARS = 5
INTRADAY_5M_PERIOD = "60d"  # Yahoo ~60d of 5m
INTRADAY_1M_PERIOD = "7d"  # Yahoo ~7d of 1m

# Pattern names emitted by detectors
PATTERN_NAMES = (
    "orb_break",
    "ema_stack_bull",
    "ema_stack_bear",
    "vwap_reclaim",
    "vwap_reject",
    "bull_engulf",
    "bear_engulf",
    "rsi_oversold_bounce",
    "rsi_overbought_fade",
    "inside_bar_break",
    "nr7_break",
)
