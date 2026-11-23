"""Short-hold F&O (options buy) plans for ₹2,000 capital.

Rules:
- BUY options only (debit) — no option selling / no futures
- Stock options = MONTHLY expiry only (no weekly on individual stocks)
- Index (NIFTY/SENSEX) may use weekly
- SAME DAY square-off by 15:10 IST
- Must fit 1 full lot inside wallet (premium × lot_size ≤ budget)
"""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import trading_config as cfg

IST = ZoneInfo("Asia/Kolkata")

# NSE stock F&O lot sizes (Nov-2026 series). Confirm on Groww before placing.
LOT_SIZE = {
    "NIFTY": 65,
    "SENSEX": 20,
    "BANKNIFTY": 30,
    "HDFCBANK": 650,
    "ICICIBANK": 700,
    "SBIN": 750,
    "AXISBANK": 625,
    "KOTAKBANK": 400,
    "ITC": 1725,
    "INFY": 400,
    "TCS": 225,
    "RELIANCE": 500,
    "TATASTEEL": 550,
    "ONGC": 1675,
    "NTPC": 1500,
    "POWERGRID": 1900,
    "COALINDIA": 1350,
    "BPCL": 1900,
    "HINDALCO": 700,
    "JSWSTEEL": 675,
    "WIPRO": 1500,
    "BHARTIARTL": 475,
    "LT": 150,
    "MARUTI": 50,
}

# Known holiday shifts (expiry moves to previous trading day)
EXPIRY_HOLIDAY_SHIFTS = {
    date(2026, 11, 24): date(2026, 11, 23),  # Tue holiday → Mon
}


def sane_option_target(
    buy_ltp: float,
    target: float | None = None,
    *,
    tgt_pct: float | None = None,
) -> float:
    """Premium-sane debit-option target. Repairs index-ATR corruption (e.g. ₹2177 on ₹5)."""
    pct = float(tgt_pct if tgt_pct is not None else getattr(cfg, "FNO_TARGET_PCT", 0.60))
    buy = float(buy_ltp or 0)
    if buy <= 0:
        return round(float(target or 0), 2)
    plan = round(buy * (1.0 + pct), 2)
    t = float(target or 0)
    cap = buy * (1.0 + pct + 0.40)  # stretch ≤ ~+100%
    if t <= buy or t > max(cap, buy * 3.0, plan * 2.0):
        return plan
    return round(t, 2)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """weekday: Mon=0 … Sun=6 (datetime). NSE stock monthly ≈ last Tuesday."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return EXPIRY_HOLIDAY_SHIFTS.get(d, d)


def next_stock_monthly_expiry(as_of: date | None = None) -> date:
    """Stock options: monthly only (no weekly)."""
    as_of = as_of or datetime.now(IST).date()
    y, m = as_of.year, as_of.month
    exp = _last_weekday_of_month(y, m, 1)  # Tuesday
    now = datetime.now(IST)
    if as_of > exp or (as_of == exp and now.hour >= 15):
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        exp = _last_weekday_of_month(y, m, 1)
    return exp


def next_index_weekly_expiry(as_of: date | None = None) -> date:
    """NIFTY/SENSEX weekly — currently Tuesday series."""
    as_of = as_of or datetime.now(IST).date()
    days = (1 - as_of.weekday()) % 7  # next Tuesday
    if days == 0 and datetime.now(IST).hour >= 15:
        days = 7
    exp = as_of + timedelta(days=days)
    return EXPIRY_HOLIDAY_SHIFTS.get(exp, exp)


def lot_size_for(code: str) -> int:
    return int(LOT_SIZE.get(code.upper(), 0))


def _strike_step(spot: float, code: str) -> float:
    code = code.upper()
    if code in {"NIFTY", "NIFTY 50", "^NSEI"}:
        return 50.0
    if code in {"BANKNIFTY", "SENSEX"}:
        return 100.0
    if spot >= 1000:
        return 20.0 if code in {"HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"} else 50.0
    if spot >= 200:
        return 5.0 if spot < 500 else 10.0
    return 2.5


def round_strike(spot: float, code: str) -> float:
    step = _strike_step(spot, code)
    return round(round(spot / step) * step, 2)


WALLET_HARD_CAP = float(cfg.CAPITAL)  # never suggest debit above ₹2000


def _estimate_weekly_premium(spot: float, strike: float, opt: str, atr: float) -> float:
    """Rough OTM premium guess so we can pick a ₹2000-fit strike (not live LTP)."""
    otm = max(0.0, (strike - spot) if opt == "CE" else (spot - strike))
    atr = max(float(atr), spot * 0.004)
    # Near ATM weekly index options are expensive (matches Groww ~₹150–300 near spot)
    base = 0.55 * atr
    est = base * math.exp(-otm / (2.2 * atr))
    return max(1.0, float(est))


def pick_affordable_strike(
    spot: float,
    code: str,
    opt: str,
    *,
    atr: float,
    max_premium_per_share: float,
) -> tuple[float, float]:
    """Walk OTM until estimated premium ≤ wallet max LTP. Returns (strike, est_premium)."""
    step = _strike_step(spot, code)
    atm = round_strike(spot, code)
    best_strike, best_est = atm, _estimate_weekly_premium(spot, atm, opt, atr)
    for i in range(0, 60):
        strike = atm + i * step if opt == "CE" else atm - i * step
        if strike <= 0:
            break
        est = _estimate_weekly_premium(spot, strike, opt, atr)
        best_strike, best_est = strike, est
        if est <= max_premium_per_share * 0.98:
            return round(strike, 2), round(est, 2)
    return round(best_strike, 2), round(best_est, 2)


def build_fno_plan(
    symbol: str,
    spot: float,
    *,
    bullish: bool,
    atr: float,
    capital: float = cfg.CAPITAL,
    as_of: date | None = None,
    option_type: str | None = None,
) -> dict:
    code = symbol.replace(".NS", "").replace("^NSEI", "NIFTY").replace("^NSEBANK", "BANKNIFTY").upper()
    if code in {"^NSEI"}:
        code = "NIFTY"
    is_index = code in {"NIFTY", "BANKNIFTY", "SENSEX"}
    if option_type:
        opt = "CE" if option_type.upper() in {"CE", "CALL"} else "PE"
    else:
        opt = "CE" if bullish else "PE"
    action = "BUY"
    expiry = next_index_weekly_expiry(as_of) if is_index else next_stock_monthly_expiry(as_of)
    lot = lot_size_for(code) or (65 if code == "NIFTY" else 30 if code == "BANKNIFTY" else 0)

    # Hard rule: total order debit ≤ min(wallet, ₹2000)
    wallet = max(0.0, min(float(capital), WALLET_HARD_CAP))
    if is_index:
        max_premium = min(wallet, WALLET_HARD_CAP)
    else:
        max_premium = min(wallet * cfg.MAX_POSITION_PCT, wallet * 0.40, wallet, WALLET_HARD_CAP)
    max_loss_rupees = min(wallet * cfg.RISK_PER_TRADE, max_premium, WALLET_HARD_CAP)

    max_premium_per_share = (max_premium / lot) if lot else 0.0
    live_meta: dict = {}
    strike = round_strike(spot, code)
    est_prem = 0.0
    # Always try live NSE chain (index + stock). Never invent LTP for BUY.
    if lot > 0 and max_premium_per_share >= 0.5:
        try:
            from marketdata.option_chain import (
                filter_chain_to_wallet,
                get_chain_cached_or_live,
                pick_affordable_from_chain,
            )

            chain = get_chain_cached_or_live(code, expiry=None, max_age_sec=60)
            # Only keep strikes that can fit this wallet — ignore the rest
            chain = filter_chain_to_wallet(
                chain, lot_size=lot, max_debit=max_premium, option_type=opt
            )
            pick = pick_affordable_from_chain(
                chain, opt, lot_size=lot, max_debit=max_premium, min_volume=1 if not is_index else 50
            )
            if pick:
                strike = float(pick["strike"])
                est_prem = float(pick["ltp"])
                if chain.get("expiry"):
                    expiry = datetime.strptime(chain["expiry"], "%d-%b-%Y").date()
                if chain.get("spot"):
                    spot = float(chain["spot"])
                src = chain.get("source") or "LIVE_NSE"
                pricing_tag = "LIVE_INDSTOCKS" if "INDSTOCKS" in str(src).upper() else "LIVE_NSE"
                live_meta = {
                    "pricing": pricing_tag,
                    "live_ltp": est_prem,
                    "live_oi": pick.get("oi"),
                    "live_volume": pick.get("volume"),
                    "chain_as_of": chain.get("as_of_label"),
                    "chain_expiry": chain.get("expiry"),
                    "from_cache": chain.get("from_cache", False),
                }
            else:
                live_meta = {
                    "pricing": "NO_LIVE_FIT",
                    "reason": (
                        f"no strike with LTP ≤ ₹{max_premium_per_share:.2f} "
                        f"(1 lot ≤ ₹{max_premium:.0f} wallet)"
                    ),
                    "chain_as_of": chain.get("as_of_label"),
                    "from_cache": chain.get("from_cache", False),
                    "wallet_filtered": True,
                }
        except Exception as exc:  # noqa: BLE001
            live_meta = {"pricing": "NO_LIVE", "reason": str(exc)[:160]}
    else:
        live_meta = {"pricing": "NO_LIVE", "reason": "lot or wallet too small"}

    # BUY only allowed when we have a real live LTP that fits the wallet
    live_ok = live_meta.get("pricing") in {"LIVE_NSE", "LIVE_INDSTOCKS"} and float(est_prem or 0) > 0
    affordable = bool(live_ok and lot > 0 and est_prem * lot <= max_premium + 1e-6 and max_premium >= 50)
    if not affordable:
        max_premium = 0.0
        max_loss_rupees = 0.0

    # Match live adverse exit so ticket SL = EXIT_NOW premium drop
    sl_pct = float(getattr(cfg, "FNO_PREMIUM_DROP_EXIT", 0.20))
    tgt_pct = float(getattr(cfg, "FNO_TARGET_PCT", 0.60))
    expiry_kind = "weekly" if is_index else "monthly"

    # Exact ₹ levels from LIVE LTP only — never pad with wallet/lot estimate
    buy_ltp = round(float(est_prem), 2) if affordable else 0.0
    buy_debit = round(buy_ltp * lot, 2) if affordable else 0.0
    # Never exceed ₹2000 hard cap
    if buy_debit > WALLET_HARD_CAP and lot > 0:
        buy_ltp = 0.0
        buy_debit = 0.0
        affordable = False
        live_meta = {**live_meta, "pricing": "OVER_CAP", "reason": "live debit exceeds ₹2000 hard cap"}
    sl_ltp = round(buy_ltp * (1 - sl_pct), 2)
    tgt_ltp = round(buy_ltp * (1 + tgt_pct), 2)
    sl_debit = round(sl_ltp * lot, 2)
    tgt_debit = round(tgt_ltp * lot, 2)
    loss_at_sl_gross = round(buy_debit - sl_debit, 2)
    profit_at_tgt_gross = round(tgt_debit - buy_debit, 2)

    # Net of Groww F&O charges — bump target until net profit, else skip
    from portfolio.charges import trade_economics

    econ_tgt = {"net_ok": False, "net_pnl": 0.0, "charges_total": 0.0, "tip": "", "breakeven_sell": tgt_ltp}
    econ_sl = {"net_pnl": 0.0, "charges_total": 0.0}
    if affordable and buy_ltp > 0 and lot > 0:
        for bump in (tgt_pct, 0.75, 0.90, 1.10, 1.40):
            cand = round(buy_ltp * (1 + bump), 2)
            trial = trade_economics(
                buy_price=buy_ltp,
                sell_price=cand,
                units=lot,
                product="NRML",
                is_option=True,
                min_net_rs=5.0,
            )
            if trial["net_ok"]:
                tgt_ltp = cand
                tgt_pct = bump
                tgt_debit = round(tgt_ltp * lot, 2)
                profit_at_tgt_gross = round(tgt_debit - buy_debit, 2)
                econ_tgt = trial
                break
            econ_tgt = trial
        else:
            affordable = False
            live_meta = {
                **live_meta,
                "pricing": live_meta.get("pricing") or "LIVE",
                "reason": econ_tgt.get("tip")
                or "target would be NET LOSS after Groww brokerage/STT/GST",
            }
        econ_sl = trade_economics(
            buy_price=buy_ltp,
            sell_price=max(sl_ltp, 0.05),
            units=lot,
            product="NRML",
            is_option=True,
            min_net_rs=0.0,
        )
        # Loss at SL includes buy+sell charges (worse than gross)
        loss_at_sl = round(abs(float(econ_sl["net_pnl"])), 2) if float(econ_sl["net_pnl"]) < 0 else loss_at_sl_gross
    else:
        loss_at_sl = loss_at_sl_gross

    profit_at_tgt = float(econ_tgt.get("net_pnl") or 0) if econ_tgt.get("net_ok") else profit_at_tgt_gross
    # Real max loss if premium goes to SL (not the tiny risk-gate stub)
    if affordable:
        max_loss_rupees = min(
            loss_at_sl,
            buy_debit + float((econ_tgt.get("buy") or {}).get("total") or 0),
            WALLET_HARD_CAP,
        )

    pricing = live_meta.get("pricing", "ESTIMATE")
    groww_hint = (
        f"{pricing}: {code} {strike:.0f} {opt} LTP ₹{buy_ltp}"
        + (f" @ {live_meta.get('chain_as_of')}" if live_meta.get("chain_as_of") else "")
        + f" · buy only if LTP ≤ ₹{max_premium_per_share:.2f} (1 lot ≤ ₹{max_premium:.0f})"
    )
    if affordable and econ_tgt.get("tip"):
        groww_hint += f" · {econ_tgt['tip']}"
    warning = (
        f"{'Index' if is_index else 'Stock'} options · 1 lot = {lot}. "
        f"Suggested {opt} strike {strike:.0f}. {groww_hint}"
    )
    if not affordable:
        why = live_meta.get("reason") or "no live LTP that fits wallet"
        warning = (
            f"SKIP {code}: {why}. Lot {lot or 'unknown'} · wallet ₹{wallet:.0f}. "
            "We never suggest a call that loses money after Groww charges."
        )

    return {
        "mode": "FNO_OPTIONS",
        "action": action,
        "option_type": opt,
        "underlying": code,
        "symbol": symbol,
        "spot": round(float(spot), 2),
        "strike": strike,
        "atm_strike": round_strike(spot, code),
        "est_premium": round(float(est_prem or 0), 2),
        "pricing": pricing,
        "live": live_meta,
        "expiry": expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry),
        "expiry_label": expiry.strftime("%d-%b-%Y") if hasattr(expiry, "strftime") else str(expiry),
        "expiry_kind": expiry_kind,
        "product": "NRML",
        "lots": 1 if affordable else 0,
        "lot_size": lot,
        "qty_shares": lot if affordable else 0,
        "max_premium_budget_rs": round(max_premium, 2),
        "max_premium_per_share": round(max_premium_per_share, 2),
        "buy_ltp": buy_ltp,
        "buy_debit_rs": buy_debit,
        "sl_ltp": sl_ltp,
        "sl_debit_rs": sl_debit,
        "target_ltp": tgt_ltp,
        "target_debit_rs": tgt_debit,
        "loss_at_sl_rs": loss_at_sl,
        "loss_at_sl_gross_rs": loss_at_sl_gross,
        "profit_at_tgt_rs": round(profit_at_tgt, 2),
        "profit_at_tgt_gross_rs": profit_at_tgt_gross,
        "charges_round_trip_rs": float(econ_tgt.get("charges_total") or 0),
        "breakeven_sell_ltp": float(econ_tgt.get("breakeven_sell") or 0),
        "net_edge_ok": bool(econ_tgt.get("net_ok")),
        "economics": econ_tgt if isinstance(econ_tgt, dict) else {},
        "affordable": affordable,
        "max_loss_rs": round(max_loss_rupees, 2),
        "sl_pct_of_premium": sl_pct,
        "target_pct_of_premium": tgt_pct,
        "sl_pct_label": str(int(sl_pct * 100)),
        "tgt_pct_label": str(int(round(tgt_pct * 100))),
        "sl_label": f"SELL at ₹{sl_ltp} (−{int(sl_pct*100)}%)",
        "target_label": f"SELL at ₹{tgt_ltp} (+{int(round(tgt_pct*100))}%) NET of Groww charges",
        "hold": "SAME_DAY_ONLY",
        "enter_after_ist": "09:46",
        "square_off_by_ist": "15:10",
        "duration_label": "Same session only · exit by 15:10 IST",
        "max_hold": "Same trading session only (do not hold overnight)",
        "groww_hint": groww_hint,
        "exit_rules": (
            [
                f"BUY 1 lot ({lot}) {code} {strike:.0f} {opt} expiry {expiry.strftime('%d-%b-%Y')}",
                f"Buy LTP ≈ ₹{buy_ltp} → premium ₹{buy_debit} + buy charges (wallet debit higher)",
                f"SL SELL LTP ₹{sl_ltp} → net loss ≈ ₹{loss_at_sl} (incl. Groww charges)",
                f"Target SELL LTP ₹{tgt_ltp} → gross ₹{profit_at_tgt_gross} · NET +₹{profit_at_tgt:.2f} after charges ₹{econ_tgt.get('charges_total', 0)}",
                f"Breakeven sell ≥ ₹{econ_tgt.get('breakeven_sell', tgt_ltp)} (else NET LOSS on Groww)",
                "Square off by 15:10 IST same day — no overnight",
            ]
            if affordable
            else [warning]
        ),
        "underlying_atr": round(float(atr), 2),
        "warning": warning,
    }


def fno_local_probability(
    *,
    option_type: str,
    bullish: bool,
    spot: float,
    strike: float,
    atr: float,
    plan: dict | None = None,
    regime: dict | None = None,
) -> float:
    """Local F&O *score* (not a measured probability) — bias + regime + liquidity.

    Returns ~[0.35, 0.72]. UI must label this as score / unproven until shadow
    book replaces it with measured win rates.
    """
    opt = (option_type or "CE").upper()
    preferred = (bullish and opt == "CE") or ((not bullish) and opt == "PE")
    base = 0.54 if preferred else 0.42

    plan = plan or {}
    live = plan.get("live") if isinstance(plan.get("live"), dict) else {}
    vol = float(live.get("live_volume") or live.get("volume") or 0)
    oi = float(live.get("live_oi") or live.get("oi") or 0)
    # Liquidity bump — thin options get penalized
    if vol >= 1_000_000 or oi >= 50_000:
        base += 0.06
    elif vol >= 200_000 or oi >= 10_000:
        base += 0.03
    elif vol > 0 or oi > 0:
        base += 0.01
    else:
        base -= 0.04

    pricing = str(plan.get("pricing") or live.get("pricing") or "").upper()
    if pricing.startswith("LIVE"):
        base += 0.03
    elif pricing in {"ESTIMATE", ""}:
        base -= 0.05

    # Distance from ATM in ATR units — far OTM is lottery; near ATM eats wallet
    atr_v = max(float(atr or 0), 1e-6)
    dist = abs(float(strike) - float(spot)) / atr_v
    if dist <= 0.35:
        base -= 0.04  # too close / expensive
    elif dist <= 1.2:
        base += 0.04  # sweet OTM for ₹2k debit
    elif dist <= 2.5:
        base += 0.01
    else:
        base -= 0.08  # deep OTM lottery

    tag = str((regime or {}).get("tag") or "range").lower()
    if tag == "high_vol":
        base -= 0.08
    elif tag == "trend":
        nifty_ret = (regime or {}).get("nifty_ret")
        if nifty_ret is not None:
            if opt == "CE" and float(nifty_ret) > 0:
                base += 0.04
            elif opt == "PE" and float(nifty_ret) < 0:
                base += 0.04
            elif preferred:
                base += 0.02
            else:
                base -= 0.03
        elif preferred:
            base += 0.03
    elif tag == "range":
        # Quiet range — debit options bleed; slight haircut unless preferred + liquid
        base -= 0.03 if not preferred else 0.01

    if plan.get("net_edge_ok") is False:
        base -= 0.06
    if plan.get("affordable") is False:
        base = min(base, 0.40)

    return round(max(0.35, min(0.72, base)), 3)


def fno_clipboard(plan: dict) -> str:
    if not plan.get("affordable"):
        return (
            f"StockScope F&O — SKIP\n"
            f"{plan.get('warning', '1 lot does not fit ₹2000 wallet')}\n"
            f"Use Normal equity tab, or check NIFTY weekly on Groww."
        )
    return (
        f"StockScope F&O (SAME DAY)\n"
        f"{plan['action']} {plan['underlying']} {plan['strike']:.0f} {plan['option_type']} "
        f"expiry {plan['expiry_label']} ({plan.get('expiry_kind', 'monthly')})\n"
        f"1 lot = {plan.get('lot_size', 0)}\n"
        f"BUY LTP ₹{plan.get('buy_ltp')} → debit ₹{plan.get('buy_debit_rs')}\n"
        f"SL SELL LTP ₹{plan.get('sl_ltp')} → ₹{plan.get('sl_debit_rs')} (loss ₹{plan.get('loss_at_sl_rs')})\n"
        f"TARGET SELL LTP ₹{plan.get('target_ltp')} → ₹{plan.get('target_debit_rs')} (profit ₹{plan.get('profit_at_tgt_rs')})\n"
        f"Max LTP cap ₹{plan.get('max_premium_per_share')} (wallet ₹{plan.get('max_premium_budget_rs')})\n"
        f"Enter after {plan['enter_after_ist']} IST | SQUARE OFF BY {plan['square_off_by_ist']} IST\n"
        + "\n".join(f"- {r}" for r in plan["exit_rules"])
    )
