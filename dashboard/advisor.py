"""Conversational F&O advisor — wallet-capped buy / skip suggestions."""

from __future__ import annotations

from config import trading_config as cfg
from marketdata.option_chain import get_chain_cached_or_live
from signals.fno import LOT_SIZE, build_fno_plan
from portfolio.models import PortfolioSettings


def wallet_now() -> dict:
    ps = PortfolioSettings.get_solo()
    equity = float(ps.equity)
    cash = float(ps.cash)
    cap = float(cfg.CAPITAL)
    usable = min(equity, cash, cap)
    return {
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "wallet": round(usable, 2),
        "hard_cap": cap,
        "life_status": ps.life_status,
    }


STEPS = [
    {
        "id": "symbol",
        "question": "Which underlying do you want to trade?",
        "choices": [
            {"id": "NIFTY", "label": "NIFTY (lot 65)"},
            {"id": "BANKNIFTY", "label": "BANKNIFTY (lot 30)"},
            {"id": "COALINDIA", "label": "COALINDIA (lot 1350)"},
            {"id": "HDFCBANK", "label": "HDFCBANK (lot 650)"},
            {"id": "ITC", "label": "ITC (lot 1725)"},
            {"id": "SBIN", "label": "SBIN (lot 750)"},
            {"id": "RELIANCE", "label": "RELIANCE (lot 500)"},
            {"id": "TATASTEEL", "label": "TATASTEEL (lot 550)"},
        ],
    },
    {
        "id": "side",
        "question": "CALL or PUT?",
        "choices": [
            {"id": "CE", "label": "CALL (CE) — bullish"},
            {"id": "PE", "label": "PUT (PE) — bearish"},
        ],
    },
    {
        "id": "style",
        "question": "How do you want the strike chosen?",
        "choices": [
            {"id": "auto", "label": "Auto — best liquid strike that fits my wallet"},
            {"id": "pick", "label": "I will pick a strike from live prices"},
        ],
    },
]


def start_session() -> dict:
    w = wallet_now()
    return {
        "ok": True,
        "step": 0,
        "answers": {},
        "wallet": w,
        "bot": (
            f"Hi — wallet ₹{w['wallet']:.0f} (hard max ₹{w['hard_cap']:.0f}). "
            "I’ll ask a few questions, show live CALL/PUT prices, then say BUY or SKIP."
        ),
        "prompt": STEPS[0],
    }


def _live_strikes(symbol: str, side: str, wallet: float, limit: int = 12, *, force: bool = False) -> dict:
    """Only return strikes that can fit wallet (LTP × lot ≤ wallet)."""
    from marketdata.option_chain import filter_chain_to_wallet

    lot = LOT_SIZE.get(symbol, 65)
    max_ltp = wallet / lot if lot else 0
    # Early exit: even ₹0.05 premium won't fit 1 lot
    if lot <= 0 or max_ltp < 0.05:
        return {
            "spot": 0,
            "expiry": None,
            "as_of": None,
            "lot": lot,
            "max_ltp": round(max_ltp, 2),
            "rows": [],
            "wallet_only": True,
            "empty_reason": f"1 lot ({lot}) needs LTP ≤ ₹{max_ltp:.2f} — nothing can fit wallet ₹{wallet:.0f}",
            "pricing": None,
            "source": None,
            "from_cache": False,
        }

    chain = get_chain_cached_or_live(symbol, max_age_sec=0 if force else 45)
    chain = filter_chain_to_wallet(chain, lot_size=lot, max_debit=wallet, option_type=side)
    spot = float(chain.get("spot") or 0)
    out = []
    for r in chain.get("rows") or []:
        ltp = float(r["ce_ltp"] if side == "CE" else r["pe_ltp"])
        vol = int(r["ce_vol"] if side == "CE" else r["pe_vol"])
        oi = int(r["ce_oi"] if side == "CE" else r["pe_oi"])
        strike = float(r["strike"])
        if ltp <= 0 or ltp > max_ltp + 1e-9:
            continue
        otm = (strike - spot) if side == "CE" else (spot - strike)
        if otm < -1e-6:  # skip ITM for debit plans
            continue
        out.append(
            {
                "strike": strike,
                "ltp": round(ltp, 2),
                "debit": round(ltp * lot, 2),
                "fits_wallet": True,
                "oi": oi,
                "volume": vol,
                "otm": round(otm, 2),
            }
        )
    from dashboard.ranking import TOP_N, score_option_row

    for row in out:
        meta = score_option_row(
            ltp=row["ltp"],
            debit=row["debit"],
            lot=lot,
            wallet=wallet,
            volume=row["volume"],
            oi=row["oi"],
            otm=row["otm"],
        )
        row["rank_meta"] = meta
        row["combo_score"] = meta["score"]
    out.sort(key=lambda x: (-x["combo_score"], x["ltp"]))
    rows = out[: min(limit, TOP_N)]
    for i, row in enumerate(rows, start=1):
        row["rank_place"] = i
    return {
        "spot": spot,
        "expiry": chain.get("expiry"),
        "as_of": chain.get("as_of_label"),
        "lot": lot,
        "max_ltp": round(max_ltp, 2),
        "max_debit": round(wallet, 2),
        "rows": rows,
        "top_n": TOP_N,
        "wallet_only": True,
        "empty_reason": None
        if rows
        else f"No {side} strike with LTP ≤ ₹{max_ltp:.2f} (1 lot ≤ ₹{wallet:.0f})",
        "pricing": chain.get("source"),
        "source": chain.get("source"),
        "from_cache": chain.get("from_cache"),
    }


def advance(
    answers: dict,
    choice_id: str | None = None,
    strike: float | None = None,
    groww_ltp: float | None = None,
) -> dict:
    """Process one chat turn. answers accumulates prior choices."""
    w = wallet_now()
    answers = dict(answers or {})
    step = int(answers.get("_step") or 0)

    if choice_id and step < len(STEPS):
        key = STEPS[step]["id"]
        answers[key] = choice_id
        step += 1
        answers["_step"] = step

    # After symbol+side, always attach live strip (force refresh before final pick)
    live = None
    if answers.get("symbol") and answers.get("side"):
        try:
            force = bool(answers.get("style"))  # refresh when deciding / recommending
            live = _live_strikes(answers["symbol"], answers["side"], w["wallet"], force=force)
        except Exception as exc:  # noqa: BLE001
            live = {"error": str(exc)}

    if groww_ltp is not None:
        try:
            answers["groww_ltp"] = float(groww_ltp)
        except (TypeError, ValueError):
            pass

    # Need strike pick step
    if step >= len(STEPS) and answers.get("style") == "pick" and strike is None and not answers.get("strike"):
        return {
            "ok": True,
            "step": step,
            "answers": answers,
            "wallet": w,
            "live": live,
            "bot": (
                (
                    live.get("empty_reason")
                    if live and live.get("empty_reason")
                    else (
                        f"Wallet-fit only for {answers['symbol']} {answers['side']}: "
                        f"LTP ≤ ₹{live.get('max_ltp') if live else '—'} "
                        f"(1 lot ≤ ₹{w['wallet']:.0f}). "
                        f"{len((live or {}).get('rows') or [])} strike(s)."
                    )
                )
                + " Paste Groww LTP to confirm, or pick a listed strike."
            ),
            "prompt": {
                "id": "strike",
                "question": "Pick a wallet-fit strike or paste Groww LTP",
                "choices": [],
                "pick_strike": True,
                "ask_groww_ltp": True,
            },
        }

    if strike is not None:
        answers["strike"] = float(strike)
        answers["style"] = answers.get("style") or "pick"

    # If strike chosen but Groww LTP not confirmed yet — ask once
    if (
        step >= len(STEPS)
        and answers.get("strike")
        and answers.get("groww_ltp") is None
        and answers.get("style") == "pick"
        and choice_id != "skip_groww"
    ):
        lot = LOT_SIZE.get(answers["symbol"], 65)
        return {
            "ok": True,
            "step": step,
            "answers": answers,
            "wallet": w,
            "live": live,
            "bot": (
                f"Strike {int(float(answers['strike']))} selected. "
                f"Paste the exact Groww Call/Put LTP for that strike "
                f"(1 lot = {lot}). We’ll size debit = LTP × {lot} vs wallet ₹{w['wallet']:.0f}."
            ),
            "prompt": {
                "id": "groww_ltp",
                "question": "Enter Groww LTP",
                "choices": [{"id": "skip_groww", "label": "Skip — use NSE hint only"}],
                "ask_groww_ltp": True,
                "pick_strike": False,
            },
        }

    if choice_id == "skip_groww":
        answers["groww_ltp"] = answers.get("groww_ltp")  # leave unset
        answers["_skip_groww"] = True

    # Finished → recommendation
    if step >= len(STEPS) and (
        answers.get("style") == "auto"
        or (answers.get("strike") and (answers.get("groww_ltp") is not None or answers.get("_skip_groww")))
    ):
        return _recommend(answers, w, live)

    # Next question
    if step < len(STEPS):
        msg = "Got it."
        if live and not live.get("error"):
            msg += (
                f" Spot ₹{live['spot']}, expiry {live['expiry']}, "
                f"as of {live['as_of']}."
            )
        return {
            "ok": True,
            "step": step,
            "answers": answers,
            "wallet": w,
            "live": live,
            "bot": msg + " " + STEPS[step]["question"],
            "prompt": STEPS[step],
        }

    return start_session()


def _recommend(answers: dict, w: dict, live: dict | None) -> dict:
    symbol = answers["symbol"]
    side = answers["side"]
    lot = LOT_SIZE.get(symbol, 65)
    wallet = w["wallet"]
    bullish = side == "CE"

    try:
        plan = build_fno_plan(
            symbol,
            float((live or {}).get("spot") or 0) or 25000,
            bullish=bullish,
            atr=200.0,
            capital=wallet,
            option_type=side,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "wallet": w,
            "bot": f"Could not build plan: {exc}",
            "done": True,
        }

    # Prefer user-pasted Groww LTP (you typed it — no site scraping)
    user_strike = answers.get("strike")
    groww = answers.get("groww_ltp")
    chosen = None
    if groww is not None and user_strike is not None:
        ltp_g = float(groww)
        chosen = {
            "strike": float(user_strike),
            "ltp": ltp_g,
            "debit": round(ltp_g * lot, 2),
            "fits_wallet": ltp_g * lot <= wallet + 1e-6,
            "pricing": "GROWW_PASTE",
        }
    elif user_strike and live and live.get("rows"):
        live_tag = (
            "LIVE_INDSTOCKS"
            if "INDSTOCKS" in str((live or {}).get("source") or (live or {}).get("pricing") or "").upper()
            else "LIVE_NSE"
        )
        for r in live["rows"]:
            if abs(float(r["strike"]) - float(user_strike)) < 0.1:
                chosen = {**r, "pricing": live_tag}
                break
        if chosen is None:
            try:
                chain = get_chain_cached_or_live(symbol, max_age_sec=60)
                chain_tag = (
                    "LIVE_INDSTOCKS"
                    if "INDSTOCKS" in str(chain.get("source") or "").upper()
                    else "LIVE_NSE"
                )
                for r in chain.get("rows") or []:
                    if abs(float(r["strike"]) - float(user_strike)) < 0.1:
                        ltp = float(r["ce_ltp"] if side == "CE" else r["pe_ltp"])
                        chosen = {
                            "strike": float(r["strike"]),
                            "ltp": ltp,
                            "debit": round(ltp * lot, 2),
                            "fits_wallet": ltp * lot <= wallet + 1e-6,
                            "pricing": chain_tag,
                        }
                        break
            except Exception:  # noqa: BLE001
                pass

    # Auto: prefer #1 scored wallet-fit strike from live table
    if answers.get("style") == "auto" and chosen is None and live and live.get("rows"):
        best = live["rows"][0]
        chosen = {**best, "pricing": (
            "LIVE_INDSTOCKS"
            if "INDSTOCKS" in str(live.get("source") or "").upper()
            else "LIVE_NSE"
        )}

    if answers.get("style") == "auto" and chosen is None:
        strike = plan.get("strike")
        ltp = float(plan.get("buy_ltp") or 0)
        debit = float(plan.get("buy_debit_rs") or 0)
        pricing = plan.get("pricing", "NO_LIVE")
        source_note = plan.get("live") or {}
    elif chosen is not None:
        strike = chosen["strike"]
        ltp = float(chosen["ltp"])
        debit = float(chosen["debit"])
        if chosen.get("pricing"):
            pricing = chosen["pricing"]
        elif str((live or {}).get("source", "")).upper().find("INDSTOCKS") >= 0:
            pricing = "LIVE_INDSTOCKS"
        else:
            pricing = "LIVE_NSE"
        source_note = {"ltp": ltp, "source": pricing, "chain_as_of": (live or {}).get("as_of")}
    else:
        strike = float(user_strike or plan.get("strike") or 0)
        ltp = 0.0
        debit = 0.0
        pricing = "NO_LIVE"
        source_note = {}

    fits = debit <= wallet + 1e-6 and ltp > 0
    price_ok = pricing in {"LIVE_NSE", "LIVE_INDSTOCKS", "GROWW_PASTE"} and ltp > 0
    sl_ltp = round(ltp * 0.65, 2) if ltp > 0 else 0.0
    tgt_ltp = round(ltp * 1.60, 2) if ltp > 0 else 0.0
    loss = round(debit - sl_ltp * lot, 2) if ltp > 0 else 0.0
    profit = round(tgt_ltp * lot - debit, 2) if ltp > 0 else 0.0

    if not price_ok:
        verdict = "SKIP"
        reason = (
            "No trusted price (paste Groww LTP or wait for LIVE NSE). "
            "ESTIMATE prices are disabled."
        )
    elif not fits:
        verdict = "SKIP"
        reason = (
            f"1 lot costs ₹{debit:.2f} (LTP ₹{ltp:.2f} × {lot}) which is above wallet ₹{wallet:.0f}. "
            "Pick a cheaper OTM strike or use Normal equity / NIFTY."
        )
    else:
        verdict = "BUY"
        tag = "Groww paste" if pricing == "GROWW_PASTE" else "LIVE NSE"
        reason = (
            f"{tag} fit: debit ₹{debit:.2f} ≤ ₹{wallet:.0f}. "
            f"Same-day only — square off by 15:10 IST."
        )

    top_lines = []
    for r in (live or {}).get("rows") or []:
        m = r.get("rank_meta") or {}
        top_lines.append(
            f"#{r.get('rank_place')} score {r.get('combo_score')} · "
            f"{int(r['strike'])} {side} LTP ₹{r['ltp']} debit ₹{r['debit']} · "
            f"+₹{m.get('profit_rs','?')} / risk ₹{m.get('loss_rs','?')}"
        )
    top_block = ("\nTop wallet-fit:\n" + "\n".join(top_lines)) if top_lines else ""

    bot = (
        f"**{verdict}** {symbol} {int(strike) if strike else 0} {side}\n"
        f"Pricing: {pricing} · LTP ₹{ltp:.2f} · 1 lot ({lot}) debit ₹{debit:.2f}\n"
        f"SL sell ₹{sl_ltp:.2f} (loss ≈ ₹{loss:.2f}) · Target ₹{tgt_ltp:.2f} (profit ≈ ₹{profit:.2f})\n"
        f"Wallet ₹{wallet:.0f}\n{reason}{top_block}"
    )

    return {
        "ok": True,
        "done": True,
        "wallet": w,
        "live": live,
        "answers": answers,
        "bot": bot,
        "top5": (live or {}).get("rows") or [],
        "suggestion": {
            "verdict": verdict,
            "symbol": symbol,
            "option_type": side,
            "strike": strike,
            "lot": lot,
            "ltp": ltp,
            "debit": debit,
            "sl_ltp": sl_ltp,
            "target_ltp": tgt_ltp,
            "loss_rs": loss,
            "profit_rs": profit,
            "pricing": pricing,
            "wait": "09:46–14:00 IST",
            "square_off": "15:10 IST same day",
            "reason": reason,
            "meta": source_note,
            "rank_meta": (chosen or {}).get("rank_meta") if isinstance(chosen, dict) else None,
        },
        "prompt": {
            "id": "again",
            "question": "Want another suggestion?",
            "choices": [
                {"id": "restart", "label": "Start over"},
            ],
        },
    }
