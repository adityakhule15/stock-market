"""Groww deep links — single Place order (SIDE) action sized to wallet."""

from __future__ import annotations

from urllib.parse import quote_plus

from signals.fno import fno_clipboard

GROWW_SLUGS = {
    "RELIANCE.NS": "reliance-industries-ltd",
    "TCS.NS": "tata-consultancy-services-ltd",
    "INFY.NS": "infosys-ltd",
    "ITC.NS": "itc-ltd",
    "TATASTEEL.NS": "tata-steel-ltd",
    "SBIN.NS": "state-bank-of-india",
    "ONGC.NS": "oil-natural-gas-corporation-ltd",
    "NTPC.NS": "ntpc-ltd",
    "POWERGRID.NS": "power-grid-corporation-of-india-ltd",
    "COALINDIA.NS": "coal-india-ltd",
    "BPCL.NS": "bharat-petroleum-corporation-ltd",
    "HINDALCO.NS": "hindalco-industries-ltd",
    "JSWSTEEL.NS": "jsw-steel-ltd",
    "WIPRO.NS": "wipro-ltd",
    "AXISBANK.NS": "axis-bank-ltd",
    "HDFCBANK.NS": "hdfc-bank-ltd",
    "ICICIBANK.NS": "icici-bank-ltd",
    "BHARTIARTL.NS": "bharti-airtel-ltd",
    "LT.NS": "larsen-toubro-ltd",
    "MARUTI.NS": "maruti-suzuki-india-ltd",
}


def nse_code(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".ns", "").upper()


def _slug(symbol: str) -> str | None:
    key = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    return GROWW_SLUGS.get(key.upper() if key.endswith(".NS") else key)


def groww_stock_url(symbol: str) -> str:
    slug = _slug(symbol)
    code = nse_code(symbol)
    if slug:
        return f"https://groww.in/stocks/{slug}"
    return f"https://groww.in/search?q={quote_plus(code)}"


def groww_options_search(symbol: str, option_type: str = "CE") -> str:
    code = nse_code(symbol)
    if code in {"NIFTY", "^NSEI"}:
        code = "Nifty"
    elif code in {"BANKNIFTY", "^NSEBANK"}:
        code = "Bank Nifty"
    kind = "call" if option_type.upper() in {"CE", "CALL"} else "put"
    return f"https://groww.in/search?q={quote_plus(f'{code} {kind} option')}"


def equity_clipboard(signal) -> str:
    code = nse_code(signal.instrument.symbol)
    return (
        f"StockScope EQUITY ({signal.product})\n"
        f"{signal.side} {code} qty {signal.qty} product {signal.product}\n"
        f"Entry Stop-Limit: trigger {signal.entry_trigger} limit {signal.entry_limit}\n"
        f"Stop-Loss Stop-Limit: trigger {signal.sl_trigger} limit {signal.sl_limit}\n"
        f"Target1 limit {signal.target1} | Target2 limit {signal.target2}\n"
        f"Skip if live price beyond chase {signal.chase_limit}\n"
        f"Max loss ₹{signal.max_loss} | Prob {signal.probability} | RR_net {signal.rr_net}\n"
        f"Wallet-sized: capital used ₹{signal.capital_used}"
    )


def groww_links_for_signal(signal) -> dict:
    sym = signal.instrument.symbol
    is_fno = signal.strategy == "fno_intraday"
    fno = {}
    if isinstance(signal.reasons, dict):
        fno = signal.reasons.get("fno") or {}

    if is_fno:
        opt = fno.get("option_type", "CE")
        detail = "BUY CALL" if opt == "CE" else "BUY PUT"
        url = groww_options_search(sym, opt)
        clip = fno_clipboard(fno) if fno else (
            f"StockScope F&O SAME DAY — {nse_code(sym)} · square off by 15:10 IST"
        )
        btn_class = "groww-call" if opt == "CE" else "groww-put"
        mode = "fno"
    else:
        side = (signal.side or "BUY").upper()
        detail = "BUY" if side == "BUY" else "SELL"
        url = groww_stock_url(sym)
        clip = equity_clipboard(signal)
        btn_class = "groww-buy" if detail == "BUY" else "groww-sell"
        mode = "normal"

    return {
        "kind": mode,
        "mode": mode,
        "order_detail": detail,
        "place_label": f"Place order ({detail})",
        "primary_url": url,
        "primary_class": btn_class,
        "clipboard": clip,
        "fno": fno,
    }
