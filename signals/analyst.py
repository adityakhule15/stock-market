"""NSE-adapted trader-analyst: quarterly facts only — never votes buy/sell."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 24 * 3600.0


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > _TTL:
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


def _yahoo_ticker(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    if s in {"NIFTY", "NIFTY50", "BANKNIFTY", "SENSEX"}:
        return {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}[s]
    return f"{s}.NS"


def _pick_row(stmt, labels: tuple[str, ...]):
    if stmt is None or getattr(stmt, "empty", True):
        return None
    for lab in labels:
        if lab in stmt.index:
            return stmt.loc[lab]
    # case-insensitive fallback
    lower = {str(i).lower(): i for i in stmt.index}
    for lab in labels:
        key = lab.lower()
        if key in lower:
            return stmt.loc[lower[key]]
    return None


def _yoy(series) -> dict[str, Any] | None:
    if series is None:
        return None
    vals = [float(v) for v in list(series.dropna().values) if v == v]
    if len(vals) < 2:
        return None
    latest, prior = vals[0], vals[1]
    # Prefer same-quarter YoY when >= 5 quarters available
    if len(vals) >= 5:
        prior = vals[4]
    chg = None
    if prior not in (0, None):
        chg = (latest - prior) / abs(prior)
    return {
        "latest": latest,
        "prior": prior,
        "yoy": round(chg, 4) if chg is not None else None,
    }


def analyst_for_symbol(
    symbol: str,
    *,
    as_of: date | None = None,
    hold_days: int = 5,
) -> dict[str, Any]:
    """Facts-only quarterly snapshot. Never returns a buy/sell vote."""
    as_of = as_of or date.today()
    ticker = _yahoo_ticker(symbol)
    key = f"an:{ticker}:{as_of.isoformat()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    out: dict[str, Any] = {
        "ok": False,
        "unavailable": True,
        "ticker": ticker,
        "vote": None,  # analyst never votes
        "facts_only": True,
    }
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        q = getattr(t, "quarterly_income_stmt", None)
        if q is None or getattr(q, "empty", True):
            q = getattr(t, "quarterly_financials", None)

        rev = _yoy(_pick_row(q, ("Total Revenue", "Operating Revenue", "Revenue")))
        profit = _yoy(
            _pick_row(
                q,
                (
                    "Net Income",
                    "Net Income Common Stockholders",
                    "Net Income From Continuing Operation Net Minority Interest",
                ),
            )
        )
        eps = _yoy(_pick_row(q, ("Basic EPS", "Diluted EPS", "Normalized EBITDA")))

        # One-off heuristic: large swing in "Other Income" vs operating revenue
        other = _pick_row(q, ("Other Income Expense", "Other Non Operating Income Expenses"))
        one_off = False
        if other is not None and rev and rev.get("latest"):
            try:
                ovals = [float(v) for v in list(other.dropna().values)[:2]]
                if ovals and abs(ovals[0]) > 0.25 * abs(float(rev["latest"])):
                    one_off = True
            except Exception:  # noqa: BLE001
                one_off = False

        results_date = None
        days_to_results = None
        results_inside_trade = False
        try:
            from marketdata.events import next_results_date

            bare = symbol.upper().replace(".NS", "").replace(".BO", "")
            results_date = next_results_date(bare, as_of=as_of)
            if results_date is not None:
                days_to_results = (results_date - as_of).days
                results_inside_trade = 0 <= days_to_results <= max(1, int(hold_days))
        except Exception:  # noqa: BLE001
            pass

        out.update(
            {
                "ok": True,
                "unavailable": False,
                "revenue": rev,
                "profit": profit,
                "eps": eps,
                "one_off_flag": one_off,
                "results_date": results_date.isoformat() if results_date else None,
                "days_to_results": days_to_results,
                "results_inside_trade": results_inside_trade,
                "summary": _brief(rev, profit, eps, one_off, results_inside_trade, days_to_results),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("analyst_for_symbol %s: %s", ticker, exc)
        out["reason"] = str(exc)[:200]

    _cache_set(key, out)
    return out


def _brief(rev, profit, eps, one_off, inside, days) -> str:
    parts = []
    if rev and rev.get("yoy") is not None:
        parts.append(f"revenue YoY {rev['yoy']*100:.1f}%")
    if profit and profit.get("yoy") is not None:
        parts.append(f"profit YoY {profit['yoy']*100:.1f}%")
    if eps and eps.get("yoy") is not None:
        parts.append(f"EPS YoY {eps['yoy']*100:.1f}%")
    if one_off:
        parts.append("possible one-off items")
    if inside:
        parts.append(f"results in {days}d (inside hold)")
    return "; ".join(parts) if parts else "limited quarterly data"
