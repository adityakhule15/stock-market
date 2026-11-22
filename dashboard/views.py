from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from config import trading_config as cfg
from marketdata.service import MarketDataService
from ml.models import DriftReport, ModelVersion
from portfolio.models import EquitySnapshot, PortfolioSettings, Position
from signals.models import Signal


def signals_page(request):
    ps = PortfolioSettings.get_solo()
    watch = list(ps.fno_watchlist or [])
    return render(
        request,
        "dashboard/signals.html",
        {
            # Pass list for json_script (do not pre-dumps — that double-encodes)
            "fno_watchlist_json": watch,
            "fno_auto_analyze": bool(ps.fno_auto_analyze),
        },
    )


def advisor_page(request):
    import json

    from dashboard.ai_chat import provider_status

    return render(
        request,
        "dashboard/advisor.html",
        {"ai_status_json": json.dumps(provider_status())},
    )


def signals_partial(request):
    from django.utils import timezone

    from dashboard.groww import groww_links_for_signal
    from signals.session_stage import apply_live_entry_status, ist_session_stage

    today = timezone.localdate()
    tab = (request.GET.get("tab") or "all").lower()
    if tab not in {"all", "normal", "fno"}:
        tab = "all"

    stage_info = ist_session_stage()

    new_qs = (
        Signal.objects.filter(status=Signal.Status.NEW, created_at__date=today)
        .select_related("instrument", "model_version")
        .order_by("-created_at")[:60]
    )
    # Also include recent NEW if none today (overnight scan edge) — but not after CLOSED
    # when we prefer tomorrow plan over stale other-day tickets
    if not new_qs.exists() and stage_info["stage"] not in {"CLOSED", "SQUARE_OFF"}:
        new_qs = (
            Signal.objects.filter(status=Signal.Status.NEW)
            .select_related("instrument", "model_version")
            .order_by("-created_at")[:40]
        )
    blocked_qs = (
        Signal.objects.filter(status=Signal.Status.SKIPPED, created_at__date=today)
        .select_related("instrument", "model_version")
        .order_by("-created_at")[:60]
    )
    signals = list(new_qs) + [s for s in blocked_qs if s.id not in {x.id for x in new_qs}]

    from dashboard.ranking import (
        TOP_N,
        life_top_n,
        mark_sticky_picks,
        rank_for_life,
        rank_signals,
    )
    from marketdata.regime import detect_regime
    from portfolio.models import PortfolioSettings, Position

    ps = PortfolioSettings.get_solo()
    # Always surface calls the user already took (open book) — never disappear on re-scan
    open_positions = list(
        Position.objects.filter(status=Position.Status.OPEN)
        .select_related("instrument", "signal")
        .order_by("-opened_at")[:20]
    )
    watchlist = {str(x).upper() for x in (ps.fno_watchlist or []) if x}
    regime = detect_regime()
    top_limit = life_top_n(ps.life_status)

    def _fno_underlying(sig) -> str:
        reasons = sig.reasons if isinstance(sig.reasons, dict) else {}
        fno = reasons.get("fno") if isinstance(reasons.get("fno"), dict) else {}
        return str(fno.get("underlying") or sig.instrument.symbol or "").upper().replace(".NS", "")

    if tab == "normal":
        signals = [s for s in signals if s.strategy != "fno_intraday"]
    elif tab == "fno":
        signals = [
            s
            for s in signals
            if s.strategy == "fno_intraday" and _fno_underlying(s) in watchlist
        ]
    else:
        # All: Normal + F&O watchlist only (never show off-watchlist F&O)
        signals = [
            s
            for s in signals
            if s.strategy != "fno_intraday" or _fno_underlying(s) in watchlist
        ]

    wallet = float(min(ps.equity, ps.cash, cfg.CAPITAL))
    hard_cap = float(cfg.CAPITAL)  # ₹2000 max per order
    affordable = []
    expired_board: list = []
    persist_ids: list[int] = []
    for s in signals:
        used = float(s.capital_used or 0)
        if used > min(wallet, hard_cap) + 1e-6:
            continue  # never show orders bigger than ₹2000 wallet
        s.groww = groww_links_for_signal(s)
        s.ticket_kind = "fno" if s.strategy == "fno_intraday" else "normal"
        s.wallet = round(wallet, 2)
        reasons = s.reasons if isinstance(s.reasons, dict) else {}
        s.ref_price = reasons.get("ref_price")
        # Live IST wait/hold — never trust frozen scan-time window after cutoff
        if s.ticket_kind == "fno":
            s.wait_range = stage_info["wait_range_fno"]
            s.hold_until = stage_info["hold_until_fno"]
        else:
            s.wait_range = stage_info["wait_range"]
            s.hold_until = stage_info["hold_until"]
        s.data_as_of_label = reasons.get("data_as_of_label")
        live_status = apply_live_entry_status(
            stored=s.entry_status,
            strategy=s.strategy or "",
            stage_info=stage_info,
        )
        if live_status != (s.entry_status or "").lower():
            s.entry_status = live_status
            persist_ids.append(int(s.id))
        else:
            s.entry_status = live_status
        # Backfill exact F&O ₹ levels for older tickets missing buy_ltp
        fno = (s.groww or {}).get("fno") or reasons.get("fno") or {}
        if fno and not fno.get("buy_ltp") and fno.get("max_premium_per_share") and fno.get("lot_size"):
            buy = float(fno["max_premium_per_share"])
            lot = int(fno["lot_size"])
            sl_pct = float(fno.get("sl_pct_of_premium") or 0.35)
            tgt_pct = float(fno.get("target_pct_of_premium") or 0.60)
            fno["buy_ltp"] = round(buy, 2)
            fno["buy_debit_rs"] = round(buy * lot, 2)
            fno["sl_ltp"] = round(buy * (1 - sl_pct), 2)
            fno["target_ltp"] = round(buy * (1 + tgt_pct), 2)
            fno["sl_debit_rs"] = round(fno["sl_ltp"] * lot, 2)
            fno["target_debit_rs"] = round(fno["target_ltp"] * lot, 2)
            fno["loss_at_sl_rs"] = round(fno["buy_debit_rs"] - fno["sl_debit_rs"], 2)
            fno["profit_at_tgt_rs"] = round(fno["target_debit_rs"] - fno["buy_debit_rs"], 2)
            s.groww["fno"] = fno
        # Heal tickets where equity exit planner wrote underlying-ATR stops onto premium
        if s.ticket_kind == "fno" and fno.get("buy_ltp") and fno.get("sl_ltp") and fno.get("target_ltp"):
            buy = float(fno["buy_ltp"])
            sl = float(fno["sl_ltp"])
            tgt = float(fno["target_ltp"])
            broken = float(s.sl_trigger or 0) <= 0 or float(s.sl_trigger or 0) > buy or float(s.target1 or 0) < buy
            if broken and sl > 0 and tgt > buy:
                s.entry_trigger = buy
                s.entry_limit = buy
                s.sl_trigger = sl
                s.sl_limit = sl
                s.target1 = tgt
                s.target2 = tgt
                try:
                    Signal.objects.filter(pk=s.id).update(
                        entry_trigger=buy,
                        entry_limit=buy,
                        sl_trigger=sl,
                        sl_limit=sl,
                        target1=tgt,
                        target2=tgt,
                    )
                except Exception:  # noqa: BLE001
                    pass
        if not s.data_as_of_label and s.data_as_of:
            try:
                s.data_as_of_label = timezone.localtime(s.data_as_of).strftime("%d-%b-%Y")
            except Exception:  # noqa: BLE001
                s.data_as_of_label = str(s.data_as_of)[:10]
        # Short human gate line (hide overnight noise)
        gate_reasons = []
        if isinstance(s.gate, dict):
            gate_reasons = list(s.gate.get("blocked_by") or [])
        soft = {"data too old", "outside entry window"}
        hard = [r for r in gate_reasons if r not in soft and not str(r).startswith("watch —")]
        if live_status == "expired":
            s.gate_summary = "entry window closed"
            s.skip_reason = s.skip_reason or "entry window closed"
        elif hard:
            labels = []
            for r in hard[:2]:
                if "probability" in r or "prob/RR" in r:
                    labels.append("low probability")
                elif "Time Machine" in r:
                    labels.append("no edge yet")
                elif "max loss" in r:
                    labels.append("risk too high")
                elif "reward:risk" in r or "RR" in r:
                    labels.append("RR too low")
                else:
                    labels.append(r)
            s.gate_summary = " · ".join(dict.fromkeys(labels))
        elif gate_reasons:
            s.gate_summary = "plan for next session"
        else:
            s.gate_summary = ""
        # After 14:00: F&O same-day tickets leave actionable board
        if not stage_info["fno_actionable"] and s.ticket_kind == "fno":
            expired_board.append(s)
        elif live_status == "expired" and s.ticket_kind == "fno":
            expired_board.append(s)
        elif stage_info["stage"] in {"CLOSED", "SQUARE_OFF"} and s.ticket_kind == "fno":
            expired_board.append(s)
        else:
            # Normal CNC can still show as plan after cutoff (multi-day) but not as "WAITING enter now"
            if live_status == "expired" and s.ticket_kind == "normal" and stage_info["stage"] in {
                "NO_NEW_ENTRY",
                "SQUARE_OFF",
                "CLOSED",
            }:
                # Keep Normal on board as plan-for-next-session, but entry_status stays expired
                affordable.append(s)
            else:
                affordable.append(s)

    # Persist EXPIRED so refresh without re-scan stays correct
    if persist_ids:
        try:
            Signal.objects.filter(id__in=persist_ids).update(
                entry_status=Signal.EntryStatus.EXPIRED
            )
        except Exception:  # noqa: BLE001
            pass

    # Life-aware cap: DEAD=0, SURVIVAL=1, ALIVE=1–3
    if ps.life_status == "dead":
        ranked, no_trade = [], "Wallet critical: stop trading, review your journal"
    elif tab == "all":
        pack = rank_for_life(affordable, wallet, life_status=ps.life_status, regime=regime)
        ranked, no_trade = pack["signals"], pack.get("no_trade")
    elif tab == "normal":
        ranked = rank_signals(
            [s for s in affordable if s.ticket_kind != "fno"], wallet, limit=max(top_limit, 1)
        )
        for s in ranked:
            s.section = "normal"
        no_trade = None if ranked else "No Normal ideas that clear net + gate filters"
    else:
        # F&O tab after cutoff: show expired section only
        if not stage_info["fno_actionable"]:
            ranked = []
            no_trade = (
                "F&O entry closed after 14:00 IST — manage open positions or Buy for tomorrow"
            )
        else:
            ranked = rank_signals(
                [s for s in affordable if s.ticket_kind == "fno"], wallet, limit=max(top_limit, 1)
            )
            for s in ranked:
                s.section = "fno"
            if ranked:
                no_trade = None
            elif affordable:
                no_trade = (
                    "F&O ideas scanned but none cleared local edge / live-pricing filters — "
                    "try Scan All or wait for a stronger setup"
                )
            else:
                no_trade = "No F&O lot fits — show Normal ideas only"

    ranked = ranked[: max(top_limit * 2, top_limit)]
    # Stick only actionable board (not expired F&O)
    try:
        if stage_info["enter_ok"] or any(s.ticket_kind != "fno" for s in ranked):
            mark_sticky_picks(ranked)
    except Exception:  # noqa: BLE001
        pass

    if stage_info.get("emphasize_tomorrow") and not ranked and not no_trade:
        no_trade = "Session closed for new F&O — use Buy for tomorrow"

    n_count = sum(1 for s in ranked if s.ticket_kind != "fno")
    f_count = sum(1 for s in ranked if s.ticket_kind == "fno")

    try:
        return render(
            request,
            "dashboard/_signal_card.html",
            {
                "signals": ranked,
                "expired_signals": expired_board[:12],
                "tab": tab,
                "wallet": wallet,
                "top_n": top_limit or TOP_N,
                "normal_count": n_count,
                "fno_count": f_count,
                "no_trade": no_trade,
                "regime": regime,
                "open_positions": open_positions,
                "session_stage": stage_info,
            },
        )
    except Exception as exc:  # noqa: BLE001
        from django.http import HttpResponse

        return HttpResponse(
            f'<p class="muted">Signals failed to render: {exc}</p>'
            f'<p class="muted">Try <strong>Scan All</strong> again.</p>',
            status=200,
            content_type="text/html",
        )


def blocked_page(request):
    signals = (
        Signal.objects.filter(status=Signal.Status.SKIPPED)
        .select_related("instrument")
        .order_by("-created_at")[:100]
    )
    return render(request, "dashboard/blocked.html", {"signals": signals})


def positions_page(request):
    from portfolio.analysis import analyze_performance
    from portfolio.day_book import build_day_book
    from portfolio.live import qty_label
    from portfolio.live_hub import get_hub
    from portfolio.services import reconcile_paper_cash, repair_open_fno_reservations

    repair_open_fno_reservations()
    reconcile_paper_cash()
    day = (request.GET.get("day") or "").strip() or None
    ps_solo = PortfolioSettings.get_solo()
    positions = list(
        Position.objects.select_related("instrument", "signal")
        .prefetch_related("fills")
        .order_by("-opened_at")[:100]
    )
    # Backfill BUY fill + Groww charges for legacy opens that predate OrderFill
    from portfolio.services import _record_fill
    from portfolio.live import is_fno, qty_units

    for p in positions:
        if p.status != Position.Status.OPEN:
            continue
        if p.fills.exists():
            continue
        try:
            product = "NRML" if is_fno(p) else (getattr(p.signal, "product", None) if p.signal_id else "CNC")
            fill = _record_fill(
                position=p,
                side="BUY" if p.side == "BUY" else "SELL",
                price=p.entry_price,
                qty=int(p.qty),
                units=qty_units(p),
                product=product or "CNC",
                source="signal_confirm",
                notes="backfilled Groww charges",
            )
            # Adjust wallet for charges not previously deducted
            from decimal import Decimal
            from portfolio.models import PortfolioSettings as PS

            extra = Decimal(str(fill.charges_total))
            ps = PS.get_solo()
            ps.cash = Decimal(ps.cash) - extra
            ps.save(update_fields=["cash", "updated_at"])
            p.reserved_cash = abs(Decimal(fill.net_cash_flow))
            p.save(update_fields=["reserved_cash"])
        except Exception:  # noqa: BLE001
            pass
    # Fresh hub snapshot on page load so LTP/P&L paint immediately (no blank "connecting…")
    hub = get_hub()
    try:
        snap = hub.force_refresh() if any(p.status == Position.Status.OPEN for p in positions) else (hub.latest()[0] or {})
    except Exception:  # noqa: BLE001
        snap, _ = hub.latest()
        snap = snap or {}
    by_id = {r.get("id"): r for r in (snap or {}).get("rows") or []}
    enriched = []
    for p in positions:
        row = by_id.get(p.id)
        if not row:
            from portfolio.live import enrich_position

            try:
                row = enrich_position(p)
            except Exception:  # noqa: BLE001
                row = {
                    "id": p.id,
                    "qty_label": qty_label(p),
                    "ltp": None,
                    "open_pnl": None,
                    "suggestion": {"code": "WAIT", "label": "WAIT — refresh live", "detail": "tap Reanalyze or reload"},
                    "contract": p.instrument.symbol,
                    "is_fno": False,
                    "status": p.status,
                }
        row = dict(row)
        row["pos"] = p
        if "status" not in row:
            row["status"] = p.status
        enriched.append(row)
    live_by_id = {int(r["id"]): r for r in enriched if r.get("id") is not None}
    day_book = build_day_book(day, live_by_id=live_by_id)
    from django.utils import timezone as dj_tz

    analysis = analyze_performance()
    analysis["cash"] = float(ps_solo.cash)
    analysis["live_as_of"] = (snap or {}).get("as_of")
    analysis["open_pnl_total"] = (snap or {}).get("open_pnl_total")
    analysis["today_pnl_gross"] = (snap or {}).get("today_pnl_gross")
    analysis["today_pnl_net"] = (snap or {}).get("today_pnl_net")
    analysis["equity_mark"] = (snap or {}).get("equity_mark")
    # Convert non-JSON-safe values for json_script
    import json
    from django.core.serializers.json import DjangoJSONEncoder

    live_snap = json.loads(json.dumps(snap or {}, cls=DjangoJSONEncoder, default=str))
    return render(
        request,
        "dashboard/positions.html",
        {
            "positions": enriched,
            "analysis": analysis,
            "live_snap": live_snap,
            "day_book": day_book,
            "day_book_date": day_book["date"],
            "day_book_is_today": day_book["date"] == dj_tz.localdate().isoformat(),
            "live_refresh_sec": int(ps_solo.live_refresh_sec or 10),
        },
    )


def performance_page(request):
    from portfolio.services import paper_gates_passed, refresh_life_status

    snaps = list(EquitySnapshot.objects.order_by("ts")[:500])
    closed = Position.objects.filter(status=Position.Status.CLOSED, mode="paper")
    pnls = [float(p.pnl) for p in closed]
    wins = sum(1 for p in pnls if p > 0)
    ps = refresh_life_status(PortfolioSettings.get_solo(), alert=False)
    from portfolio.analysis import analyze_performance

    paper_ok = paper_gates_passed()
    analysis = analyze_performance(mode="paper")
    ctx = {
        "snaps": snaps,
        "n_trades": len(pnls),
        "hit_rate": (wins / len(pnls) * 100) if pnls else 0,
        "total_pnl": sum(pnls),
        "equity": ps.equity,
        "start_capital": cfg.CAPITAL,
        "paper_min": cfg.PAPER_MIN_SIGNALS,
        "analysis": analysis,
        "go_live": {
            "backtest_ok": False,
            "paper_ok": paper_ok,
            "live_unlocked": paper_ok,
            "kill_armed": True,
        },
    }
    return render(request, "dashboard/performance.html", ctx)


def models_page(request):
    models = ModelVersion.objects.order_by("-created_at")[:50]
    drifts = DriftReport.objects.order_by("-created_at")[:20]
    return render(request, "dashboard/models.html", {"models": models, "drifts": drifts})


@require_http_methods(["GET", "POST"])
def backtest_page(request):
    result = None
    error = None
    symbol = request.POST.get("symbol", "RELIANCE.NS") if request.method == "POST" else "RELIANCE.NS"
    if request.method == "POST":
        try:
            from backtest.engine import run_backtest
            from ml import registry

            champ = registry.champion()
            if champ is None:
                error = "No champion model. Run: python manage.py train --mode swing"
            else:
                svc = MarketDataService()
                bars = svc.bars_df(symbol, interval="1d")
                result = run_backtest(symbol, bars, registry.load(champ))
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    return render(
        request,
        "dashboard/backtest.html",
        {"result": result, "error": error, "symbol": symbol},
    )


def settings_page(request):
    from marketdata import indstocks as ind
    from portfolio.services import reconcile_paper_cash, refresh_life_status

    ps = refresh_life_status(PortfolioSettings.get_solo(), alert=False)
    reconcile_paper_cash()
    tok = ind.token_status()
    return render(
        request,
        "dashboard/settings.html",
        {
            "cfg": cfg,
            "portfolio": ps,
            "indstocks_token": bool(tok.get("has_token")),
            "indstocks_token_status": tok,
            "allow_dead_content": True,
        },
    )


def journal_page(request):
    from portfolio.day_book import build_day_book
    from portfolio.models import TradeReview
    from portfolio.reviews import discipline_score, top_mistake_tags

    day = (request.GET.get("day") or "").strip() or None
    day_book = build_day_book(day)
    reviews = (
        TradeReview.objects.select_related("position", "position__instrument")
        .order_by("-created_at")[:100]
    )
    if day_book.get("date"):
        from datetime import date as date_cls

        d = date_cls.fromisoformat(day_book["date"])
        reviews = (
            TradeReview.objects.filter(created_at__date=d)
            .select_related("position", "position__instrument")
            .order_by("-created_at")[:100]
        )
    closed = Position.objects.filter(status=Position.Status.CLOSED).order_by("-closed_at")[:50]
    return render(
        request,
        "dashboard/journal.html",
        {
            "reviews": reviews,
            "closed": closed,
            "discipline": discipline_score(),
            "top_mistakes": top_mistake_tags(),
            "day_book": day_book,
            "day_book_date": day_book["date"],
            "allow_dead_content": True,
        },
    )


def journal_weekly_page(request):
    from collections import Counter
    from datetime import timedelta

    from django.utils import timezone

    from portfolio.models import TradeReview
    from portfolio.reviews import discipline_score

    since = timezone.now() - timedelta(days=7)
    closed = Position.objects.filter(status=Position.Status.CLOSED, closed_at__gte=since)
    pnls = [float(p.pnl) for p in closed]
    wins = sum(1 for p in pnls if p > 0)
    reviews = TradeReview.objects.filter(created_at__gte=since)
    tag_c: Counter[str] = Counter()
    for r in reviews:
        for t in r.mistake_tags or []:
            if t != "followed_signal_correctly":
                tag_c[t] += 1
    avg_r = 0.0
    rs = []
    for r in reviews:
        m = r.planned_vs_actual or {}
        if "r_achieved" in m:
            rs.append(float(m["r_achieved"]))
    if rs:
        avg_r = sum(rs) / len(rs)
    lessons = []
    for tag, n in tag_c.most_common(3):
        lessons.append(f"Watch for «{tag}» — showed up {n} time(s) this week.")
    if not lessons:
        lessons = [
            "Keep logging reviews after every close.",
            "Protect the DEAD floor — size never breaches it.",
            "Best trade can be no trade on coin-flip days.",
        ]
    disc = discipline_score(limit=100)
    return render(
        request,
        "dashboard/journal_weekly.html",
        {
            "n_trades": len(pnls),
            "total_pnl": sum(pnls) if pnls else 0,
            "win_rate": (wins / len(pnls) * 100) if pnls else 0,
            "avg_r": round(avg_r, 2),
            "top_mistakes": tag_c.most_common(5),
            "discipline": disc,
            "lessons": lessons[:3],
            "allow_dead_content": True,
        },
    )


def insights_page(request):
    from ml.meta_filter import calibration_report, rule_level_stats

    return render(
        request,
        "dashboard/insights.html",
        {
            "calibration": calibration_report(),
            "rules": rule_level_stats(),
            "allow_dead_content": True,
        },
    )
