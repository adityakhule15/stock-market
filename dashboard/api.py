from __future__ import annotations

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from config import trading_config as cfg
from dashboard.serializers import (
    DriftReportSerializer,
    EquitySnapshotSerializer,
    ModelVersionSerializer,
    PositionSerializer,
    SignalSerializer,
)
from marketdata.models import JobRun
from marketdata.service import MarketDataService
from ml import registry
from ml.models import DriftReport, ModelVersion
from portfolio.models import EquitySnapshot, PortfolioSettings, Position
from portfolio.analysis import analyze_performance
from portfolio.services import (
    add_manual_groww_order,
    arm_kill_switch,
    close_position,
    confirm_skip,
    delete_position,
    edit_position,
    get_portfolio_state,
    mark_signal_taken,
    paper_gates_passed,
    reanalyze_position,
    reconcile_paper_cash,
    reset_life_status,
    set_position_dummy,
)
from signals.models import Signal


class SignalViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SignalSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = Signal.objects.select_related("instrument", "model_version").order_by("-created_at")
        st = self.request.query_params.get("status")
        if st:
            qs = qs.filter(status=st)
        return qs

    @action(detail=False, methods=["post"])
    def scan(self, request):
        """Manual scan.

        mode=normal (default): equity universe only.
        mode=fno: PortfolioSettings.fno_watchlist only.
        mode=all|both: Normal equity scan + F&O watchlist in one request.
        """
        from decimal import Decimal

        from marketdata.management.commands.bootstrap import BOOTSTRAP_SYMBOLS
        from marketdata.universe import shortlist_symbols
        from signals.fno import LOT_SIZE
        from signals.tasks import generate_fno_for_underlyings, generate_signals_for_universe

        ps = PortfolioSettings.get_solo()
        if float(ps.equity) <= 0 or float(ps.cash) <= 0:
            ps.equity = Decimal(str(cfg.CAPITAL))
            ps.cash = Decimal(str(cfg.CAPITAL))
            ps.save(update_fields=["equity", "cash"])

        mode = str((request.data or {}).get("mode") or "normal").lower()
        names: list[str] = []
        created: list = []
        created_normal: list = []
        created_fno: list = []
        try:
            run_normal = mode in {"normal", "all", "both"}
            run_fno = mode in {"fno", "all", "both"}

            if run_fno:
                # F&O analyze is watchlist-only (no one-off symbols from UI)
                names = [str(x).upper().replace(".NS", "") for x in (ps.fno_watchlist or []) if x]
                names = list(dict.fromkeys(names))
                if mode == "fno" and not names:
                    return Response(
                        {
                            "ok": False,
                            "error": "F&O watchlist empty — add underlyings in Settings.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if names:
                    bad = [n for n in names if n not in LOT_SIZE and n not in {"NIFTY", "BANKNIFTY", "SENSEX"}]
                    if bad:
                        return Response(
                            {"ok": False, "error": f"Unknown F&O underlying(s): {', '.join(bad)}"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    created_fno = generate_fno_for_underlyings(names)

            if run_normal:
                symbols = shortlist_symbols(40) or BOOTSTRAP_SYMBOLS
                merged = list(dict.fromkeys(list(symbols) + list(BOOTSTRAP_SYMBOLS)))
                created_normal = generate_signals_for_universe(merged[:25])

            created = list(created_normal) + list(created_fno)
            if mode in {"all", "both"} and not names and not created_normal:
                return Response(
                    {
                        "ok": False,
                        "error": "Nothing to scan — F&O watchlist empty and equity scan returned no tickets.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        passed = sum(1 for s in created if s.status == Signal.Status.NEW)
        wallet = float(min(PortfolioSettings.get_solo().equity, PortfolioSettings.get_solo().cash, cfg.CAPITAL))
        out_mode = "all" if mode in {"all", "both"} else mode
        return Response(
            {
                "ok": True,
                "mode": out_mode,
                "underlyings": names if run_fno else None,
                "underlying": (names[0] if run_fno and names else None),
                "created": len(created),
                "created_normal": len(created_normal),
                "created_fno": len(created_fno),
                "passed": passed,
                "blocked": len(created) - passed,
                "wallet": wallet,
                "ids": [s.id for s in created],
                "warning": (
                    "F&O watchlist empty — scanned Normal only."
                    if out_mode == "all" and not names
                    else None
                ),
            }
        )

    @action(detail=True, methods=["post"], url_path="mark-taken")
    def mark_taken(self, request, pk=None):
        """Confirm: did you place this order? Send fill_price from your broker."""
        signal = self.get_object()
        mode = request.data.get("mode", "paper")
        if cfg.FORCE_PAPER_UNTIL_GATES and mode == "live" and not paper_gates_passed():
            mode = "paper"
        confirmed = request.data.get("order_confirmed", True)
        if confirmed in (False, "false", "0", 0, "no"):
            confirm_skip(signal, reason=request.data.get("reason", "I did not place this order"))
            return Response({"ok": True, "skipped": True, "signal": SignalSerializer(signal).data})
        fill = request.data.get("fill_price")
        qty = request.data.get("qty")
        practice = request.data.get("practice") in (True, "true", "1", 1, "yes")
        is_dummy = practice or request.data.get("is_dummy") in (True, "true", "1", 1, "yes")
        try:
            pos = mark_signal_taken(
                signal,
                mode=mode,
                fill_price=float(fill) if fill is not None else None,
                qty=int(qty) if qty is not None else None,
                order_confirmed=True,
                is_dummy=bool(is_dummy),
            )
        except ValueError as exc:
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        analysis = analyze_performance(mode=mode)
        return Response(
            {
                "ok": True,
                "position_id": pos.id,
                "mode": mode,
                "fill_price": float(pos.entry_price),
                "signal": SignalSerializer(signal).data,
                "analysis": analysis,
            }
        )

    @action(detail=True, methods=["post"])
    def skip(self, request, pk=None):
        signal = self.get_object()
        reason = request.data.get("reason", "I did not place this order")
        confirm_skip(signal, reason=reason)
        return Response(SignalSerializer(signal).data)

    @action(detail=True, methods=["get"])
    def timemachine(self, request, pk=None):
        signal = self.get_object()
        return Response(signal.time_machine)

    @action(detail=True, methods=["get"])
    def gate(self, request, pk=None):
        signal = self.get_object()
        return Response(signal.gate)


class PositionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Position.objects.select_related("instrument", "signal").order_by("-opened_at")
    serializer_class = PositionSerializer
    permission_classes = [AllowAny]

    @action(detail=False, methods=["get"])
    def live(self, request):
        """One-shot live snapshot from the shared hub (prefer SSE /positions/stream/)."""
        from portfolio.live_hub import get_hub

        hub = get_hub()
        snap, _fp = hub.latest()
        if snap is None or request.query_params.get("refresh") == "1":
            snap = hub.force_refresh()
        return Response(snap)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Close a confirmed trade with exit price + required TradeReview fields."""
        pos = self.get_object()
        if pos.status == Position.Status.CLOSED:
            return Response({"ok": False, "error": "already closed"}, status=status.HTTP_400_BAD_REQUEST)
        exit_price = request.data.get("exit_price")
        if exit_price is None:
            return Response({"ok": False, "error": "exit_price required"}, status=status.HTTP_400_BAD_REQUEST)
        reason = request.data.get("reason", "manual")
        exit_reason = request.data.get("exit_reason") or reason
        tags = request.data.get("mistake_tags") or request.data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        # Map legacy reason=stop/target to exit_reason
        if exit_reason == "stop":
            exit_reason = "sl"
        if exit_reason == "manual":
            exit_reason = request.data.get("exit_reason") or "manual_early"
        try:
            pos = close_position(
                pos,
                float(exit_price),
                reason=str(reason),
                exit_reason=str(exit_reason),
                mistake_tags=list(tags),
                review_note=str(request.data.get("note") or request.data.get("review_note") or ""),
                require_review=True,
            )
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        analysis = analyze_performance(mode=pos.mode)
        review = None
        if hasattr(pos, "review"):
            try:
                r = pos.review
                review = {
                    "exit_reason": r.exit_reason,
                    "mistake_tags": r.mistake_tags,
                    "followed_plan": r.followed_plan,
                    "planned_vs_actual": r.planned_vs_actual,
                }
            except Exception:  # noqa: BLE001
                review = None
        return Response(
            {
                "ok": True,
                "position": PositionSerializer(pos).data,
                "pnl": float(pos.pnl),
                "analysis": analysis,
                "review": review,
            }
        )

    @action(detail=True, methods=["post"], url_path="confirm-levels")
    def confirm_levels(self, request, pk=None):
        """User updated SL/target in Groww — store + adjustment log."""
        from portfolio.services import confirm_levels_updated

        pos = self.get_object()
        try:
            stop = float(request.data.get("stop"))
            target = float(request.data.get("target"))
            pos = confirm_levels_updated(pos, stop=stop, target=target)
        except (TypeError, ValueError) as exc:
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        from portfolio.live import enrich_position

        return Response({"ok": True, "live": enrich_position(pos), "position": PositionSerializer(pos).data})

    @action(detail=True, methods=["post"], url_path="delete")
    def remove(self, request, pk=None):
        pos = self.get_object()
        try:
            out = delete_position(pos)
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, **out, "analysis": analyze_performance()})

    @action(detail=True, methods=["post"], url_path="mark-kind")
    def mark_kind(self, request, pk=None):
        """Mark position as dummy (practice) or original. Allowed after close too."""
        pos = self.get_object()
        data = request.data or {}
        kind = str(data.get("kind") or "").strip().lower()
        if "is_dummy" in data and kind not in {"dummy", "original"}:
            is_dummy = bool(data.get("is_dummy"))
        elif kind in {"dummy", "practice"}:
            is_dummy = True
        elif kind in {"original", "real", "live"}:
            is_dummy = False
        else:
            return Response(
                {"ok": False, "error": "kind must be 'dummy' or 'original'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            pos = set_position_dummy(pos, is_dummy)
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "ok": True,
                "id": pos.id,
                "is_dummy": bool(pos.is_dummy),
                "kind": "dummy" if pos.is_dummy else "original",
                "status": pos.status,
                "analysis": analyze_performance(),
            }
        )

    @action(detail=True, methods=["post"])
    def edit(self, request, pk=None):
        pos = self.get_object()
        data = request.data or {}
        try:
            pos = edit_position(
                pos,
                entry_price=float(data["entry_price"]) if data.get("entry_price") is not None else None,
                stop=float(data["stop"]) if data.get("stop") is not None else None,
                target=float(data["target"]) if data.get("target") is not None else None,
                qty=int(data["qty"]) if data.get("qty") is not None else None,
            )
        except (ValueError, TypeError) as exc:
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        from portfolio.live import enrich_position

        return Response({"ok": True, "live": enrich_position(pos), "position": PositionSerializer(pos).data})

    @action(detail=True, methods=["post"])
    def reanalyze(self, request, pk=None):
        pos = self.get_object()
        try:
            out = reanalyze_position(pos)
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, **out})

    @action(detail=True, methods=["post"])
    def advise(self, request, pk=None):
        """HOLD / SELL for this open position — local rules + model; Gemini coach notes only."""
        from dashboard.position_advise import advise_position

        pos = self.get_object()
        try:
            out = advise_position(pos)
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not out.get("ok"):
            return Response(out, status=status.HTTP_400_BAD_REQUEST)
        return Response(out)

    @action(detail=False, methods=["post"], url_path="advise-all")
    def advise_all(self, request):
        from dashboard.position_advise import advise_all_open

        try:
            out = advise_all_open()
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(out)

    @action(detail=False, methods=["post"], url_path="manual-groww")
    def manual_groww(self, request):
        """Add a dummy Groww order record (you already placed it on Groww). No broker API."""
        data = request.data or {}
        try:
            pos = add_manual_groww_order(
                symbol=str(data.get("symbol") or ""),
                side=str(data.get("side") or "BUY"),
                product=str(data.get("product") or "NRML"),
                qty=int(data.get("qty") or 1),
                lot_size=int(data["lot_size"]) if data.get("lot_size") is not None else None,
                entry_price=float(data.get("entry_price")),
                stop=float(data["stop"]) if data.get("stop") is not None else None,
                target=float(data["target"]) if data.get("target") is not None else None,
                exit_price=float(data["exit_price"]) if data.get("exit_price") not in (None, "") else None,
                option_type=(str(data.get("option_type")).upper() if data.get("option_type") else None),
                strike=float(data["strike"]) if data.get("strike") not in (None, "") else None,
                expiry=str(data.get("expiry") or ""),
                notes=str(data.get("notes") or "manual Groww record"),
                mistake_note=str(data.get("mistake_note") or ""),
                is_dummy=bool(data.get("is_dummy") or data.get("kind") == "dummy"),
            )
        except (TypeError, ValueError, KeyError) as exc:
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        from portfolio.live import enrich_position

        return Response(
            {
                "ok": True,
                "position_id": pos.id,
                "live": enrich_position(pos),
                "fills": [
                    {
                        "side": f.side,
                        "price": float(f.price),
                        "charges_total": float(f.charges_total),
                        "charges": f.charges,
                        "net_cash_flow": float(f.net_cash_flow),
                    }
                    for f in pos.fills.all().order_by("placed_at")
                ],
                "cash": float(PortfolioSettings.get_solo().cash),
                "equity": float(PortfolioSettings.get_solo().equity),
            }
        )

    @action(detail=False, methods=["get"], url_path="charges-preview")
    def charges_preview(self, request):
        """Preview Groww charges for a buy/sell (or round-trip)."""
        from portfolio.charges import calc_leg_charges, round_trip_charges

        price = float(request.query_params.get("price") or 0)
        units = int(request.query_params.get("units") or request.query_params.get("qty") or 1)
        product = str(request.query_params.get("product") or "NRML")
        is_option = request.query_params.get("is_option", "1") not in ("0", "false", "no")
        exit_p = request.query_params.get("exit_price")
        buy = calc_leg_charges(side="BUY", price=price, units=units, product=product, is_option=is_option)
        out = {"ok": True, "buy": buy.as_dict(), "wallet_debit": round(buy.turnover + buy.total, 2)}
        if exit_p not in (None, ""):
            out["round_trip"] = round_trip_charges(
                buy_price=price, sell_price=float(exit_p), units=units, product=product, is_option=is_option
            )
        return Response(out)


class ModelViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ModelVersion.objects.all().order_by("-created_at")
    serializer_class = ModelVersionSerializer
    permission_classes = [AllowAny]

    @action(detail=False, methods=["post"], permission_classes=[IsAdminUser])
    def retrain(self, request):
        from ml.retrain import retrain_and_maybe_promote

        full = bool(request.data.get("full", False))
        mv, promoted = retrain_and_maybe_promote(full=full)
        return Response({"id": mv.id, "promoted": promoted, "metrics": mv.metrics})

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def promote(self, request, pk=None):
        mv = self.get_object()
        registry.promote(mv)
        return Response(ModelVersionSerializer(mv).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def quotes(request, symbol: str):
    svc = MarketDataService()
    try:
        bar, source = svc.latest_bar(symbol)
        age_min = int((timezone.now() - bar["ts"]).total_seconds() // 60) if bar.get("ts") else None
        return Response({"symbol": symbol, "bar": {**bar, "ts": bar["ts"].isoformat()}, "source": source, "data_age_min": age_min})
    except Exception as exc:  # noqa: BLE001
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["POST"])
@permission_classes([AllowAny])
def advisor_start(request):
    from dashboard.advisor import start_session

    return Response(start_session())


@api_view(["POST"])
@permission_classes([AllowAny])
def advisor_chat(request):
    from dashboard.advisor import advance, start_session

    data = request.data or {}
    if data.get("choice_id") == "restart":
        return Response(start_session())
    result = advance(
        data.get("answers") or {},
        choice_id=data.get("choice_id"),
        strike=data.get("strike"),
        groww_ltp=data.get("groww_ltp"),
    )
    return Response(result)


@api_view(["POST"])
@permission_classes([AllowAny])
def advisor_ai_chat(request):
    """Free-form Advisor chat (Gemini / DeepSeek / local). Never places orders."""
    from dashboard.ai_chat import chat, provider_status

    data = request.data or {}
    message = data.get("message") or data.get("text") or ""
    history = data.get("history") or []
    if not isinstance(history, list):
        history = []
    # Sanitize history roles
    clean = []
    for h in history[-12:]:
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(h.get("content") or "")[:4000]
        if content:
            clean.append({"role": role, "content": content})
    result = chat(str(message)[:4000], history=clean)
    result.setdefault("status", provider_status())
    return Response(result)


@api_view(["GET"])
@permission_classes([AllowAny])
def advisor_ai_status(request):
    from dashboard.ai_chat import provider_status

    return Response({"ok": True, **provider_status()})


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def advisor_tomorrow(request):
    """What to purchase for tomorrow — local ranked tickets; AI coach optional."""
    from dashboard.tomorrow_plan import build_tomorrow_plan

    rescan = True
    use_ai = False
    if request.method == "GET":
        rescan = str(request.query_params.get("rescan") or "1") not in {"0", "false", "no"}
        use_ai = str(request.query_params.get("ai") or "0") in {"1", "true", "yes"}
    else:
        data = request.data or {}
        rescan = data.get("rescan", True) not in (False, "false", "0", 0, "no")
        use_ai = data.get("ai", False) in (True, "true", "1", 1, "yes")
    try:
        out = build_tomorrow_plan(rescan=bool(rescan), use_ai=bool(use_ai))
    except Exception as exc:  # noqa: BLE001
        return Response(
            {"ok": False, "error": str(exc), "summary": f"Tomorrow plan failed: {exc}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(out)


@api_view(["GET"])
@permission_classes([AllowAny])
def option_chain(request):
    """Live NSE option chain + local snapshot history.

    ?symbol=NIFTY|BANKNIFTY&expiry=17-Nov-2026&history=1
    """
    from marketdata.option_chain import (
        fetch_option_chain,
        get_chain_cached_or_live,
        list_expiries,
        list_snapshots,
        pick_affordable_from_chain,
    )

    symbol = (request.query_params.get("symbol") or "NIFTY").upper()
    expiry = request.query_params.get("expiry") or None
    want_history = request.query_params.get("history") in {"1", "true", "yes"}
    refresh = request.query_params.get("refresh") in {"1", "true", "yes"}
    try:
        expiries = list_expiries(symbol)
        chain = fetch_option_chain(symbol, expiry) if refresh else get_chain_cached_or_live(symbol, expiry)
        lot = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}.get(symbol, 65)
        picks = {
            "CE": pick_affordable_from_chain(chain, "CE", lot_size=lot, max_debit=float(cfg.CAPITAL)),
            "PE": pick_affordable_from_chain(chain, "PE", lot_size=lot, max_debit=float(cfg.CAPITAL)),
        }
        # Slim rows for response (optional full=1)
        full = request.query_params.get("full") in {"1", "true", "yes"}
        rows = chain.get("rows") or []
        if not full:
            # Keep strikes near spot ±8% plus affordable picks
            spot = float(chain.get("spot") or 0)
            lo, hi = spot * 0.92, spot * 1.08
            rows = [r for r in rows if lo <= r["strike"] <= hi]
        payload = {
            "ok": True,
            "symbol": symbol,
            "expiries": expiries,
            "expiry": chain.get("expiry"),
            "spot": chain.get("spot"),
            "as_of": chain.get("as_of"),
            "as_of_label": chain.get("as_of_label"),
            "source": chain.get("source"),
            "from_cache": chain.get("from_cache", False),
            "wallet_cap": cfg.CAPITAL,
            "lot_size": lot,
            "affordable_picks": picks,
            "n_strikes": chain.get("n_strikes"),
            "rows": rows,
            "note": (
                "Live LTP from NSE public option-chain. "
                "History = snapshots we save under data/option_chain/ (not multi-year vendor history)."
            ),
        }
        if want_history:
            payload["history"] = list_snapshots(symbol, limit=30)
        return Response(payload)
    except Exception as exc:  # noqa: BLE001
        return Response({"ok": False, "error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["GET"])
@permission_classes([AllowAny])
def journal_export_csv(request):
    """Export TradeReview journal as CSV."""
    import csv
    from django.http import HttpResponse

    from portfolio.models import TradeReview

    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="stockscope_journal.csv"'
    w = csv.writer(resp)
    w.writerow(
        [
            "created_at",
            "symbol",
            "exit_reason",
            "mistake_tags",
            "followed_plan",
            "pnl",
            "planned_entry",
            "actual_entry",
            "r_achieved",
            "note",
        ]
    )
    for r in TradeReview.objects.select_related("position", "position__instrument").order_by("-created_at"):
        m = r.planned_vs_actual or {}
        w.writerow(
            [
                r.created_at.isoformat(),
                r.position.instrument.symbol,
                r.exit_reason,
                "|".join(r.mistake_tags or []),
                r.followed_plan,
                float(r.position.pnl),
                m.get("planned_entry"),
                m.get("actual_entry"),
                m.get("r_achieved"),
                (r.note or "").replace("\n", " "),
            ]
        )
    return resp


@api_view(["GET"])
@permission_classes([AllowAny])
def performance(request):
    snaps = EquitySnapshot.objects.order_by("ts")[:500]
    analysis = analyze_performance(mode=request.query_params.get("mode") or None)
    return Response(
        {
            "equity_curve": EquitySnapshotSerializer(snaps, many=True).data,
            "analysis": analysis,
            "n_trades": analysis["n_closed"],
            "hit_rate": analysis["hit_rate"],
            "total_pnl": analysis["total_pnl"],
            "start_capital": cfg.CAPITAL,
            "current": analysis["equity"],
            "verdict": analysis["verdict"],
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def analysis(request):
    return Response(analyze_performance(mode=request.query_params.get("mode") or None))


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    from portfolio.services import refresh_life_status

    ps = refresh_life_status(PortfolioSettings.get_solo())
    jobs = JobRun.objects.order_by("-started_at")[:10]
    pf = get_portfolio_state()
    return Response(
        {
            "kill_switch": ps.kill_switch,
            "life_status": ps.life_status,
            "dead_floor": cfg.DEAD_FLOOR,
            "survival_equity": cfg.SURVIVAL_EQUITY,
            "equity": float(ps.equity),
            "cash": float(ps.cash),
            "open_positions": pf.open_positions,
            "force_paper": cfg.FORCE_PAPER_UNTIL_GATES and not paper_gates_passed(),
            "paper_gates_passed": paper_gates_passed(),
            "jobs": [
                {
                    "name": j.name,
                    "started_at": j.started_at,
                    "finished_at": j.finished_at,
                    "ok": j.ok,
                    "detail": j.detail,
                }
                for j in jobs
            ],
            "intraday_enabled": cfg.INTRADAY_ENABLED,
            "allow_broker_orders": bool(getattr(cfg, "ALLOW_BROKER_ORDERS", False)),
            "indstocks_data": __import__("marketdata.indstocks", fromlist=["data_enabled"]).data_enabled(),
            "indstocks_token": __import__("marketdata.indstocks", fromlist=["token_status"]).token_status(),
            "live_refresh_sec": int(getattr(ps, "live_refresh_sec", 10) or 10),
            "champion": (
                f"{c.name}_v{c.version}" if (c := registry.champion()) else None
            ),
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def day_book(request):
    """All trades for a date with gross / charges / net totals."""
    from portfolio.day_book import build_day_book

    day = request.query_params.get("date") or None
    try:
        book = build_day_book(day)
    except ValueError as exc:
        return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"ok": True, **book})


@api_view(["POST"])
@permission_classes([AllowAny])
def retrain_now(request):
    """Manual day-end retrain (LightGBM + meta filter)."""
    from marketdata.jobs import nightly_retrain

    try:
        detail = nightly_retrain()
        return Response({"ok": True, "detail": detail})
    except Exception as exc:  # noqa: BLE001
        return Response({"ok": False, "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def indstocks_token(request):
    """GET status (no secret) / POST save new JWT until expiry."""
    from marketdata import indstocks as ind

    if request.method == "GET":
        return Response({"ok": True, **ind.token_status()})
    raw = (request.data or {}).get("token") or (request.data or {}).get("access_token") or ""
    try:
        out = ind.save_access_token(str(raw))
        return Response(out)
    except ValueError as exc:
        return Response(
            {"ok": False, "error": str(exc), "help_url": ind.TOKEN_HELP},
            status=status.HTTP_400_BAD_REQUEST,
        )


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def live_refresh(request):
    """GET/POST Positions LTP refresh interval (seconds)."""
    ps = PortfolioSettings.get_solo()
    if request.method == "GET":
        return Response({"ok": True, "live_refresh_sec": int(ps.live_refresh_sec or 10)})
    try:
        sec = int((request.data or {}).get("live_refresh_sec") or (request.data or {}).get("seconds") or 10)
    except (TypeError, ValueError):
        return Response({"ok": False, "error": "seconds required"}, status=status.HTTP_400_BAD_REQUEST)
    sec = max(5, min(120, sec))
    ps.live_refresh_sec = sec
    ps.save(update_fields=["live_refresh_sec", "updated_at"])
    try:
        from portfolio.live_hub import set_refresh_sec

        set_refresh_sec(sec)
    except Exception:  # noqa: BLE001
        pass
    return Response({"ok": True, "live_refresh_sec": sec})


@api_view(["GET"])
@permission_classes([AllowAny])
def orb_today(request):
    if not cfg.INTRADAY_ENABLED:
        return Response({"enabled": False, "plans": []})
    # Plans stored as intraday signals for today
    qs = Signal.objects.filter(strategy="intraday", created_at__date=timezone.localdate()).order_by("-created_at")
    return Response({"enabled": True, "plans": SignalSerializer(qs, many=True).data})


@api_view(["POST"])
@permission_classes([AllowAny])
def set_kill_switch(request):
    """Local single-user app — AllowAny like other Settings toggles."""
    on = bool(request.data.get("on", True))
    ps = arm_kill_switch(on)
    return Response({"ok": True, "kill_switch": ps.kill_switch, "life_status": ps.life_status})


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def fno_watchlist(request):
    """GET/POST F&O always-analyze watchlist (Settings)."""
    from signals.fno import LOT_SIZE

    ps = PortfolioSettings.get_solo()
    catalog = [
        {"id": k, "label": f"{k} (lot {v})"}
        for k, v in sorted(LOT_SIZE.items(), key=lambda kv: (0 if kv[0] in {"NIFTY", "BANKNIFTY", "SENSEX"} else 1, kv[0]))
    ]
    if request.method == "GET":
        return Response(
            {
                "ok": True,
                "watchlist": list(ps.fno_watchlist or []),
                "auto_analyze": bool(ps.fno_auto_analyze),
                "catalog": catalog,
            }
        )
    data = request.data or {}
    raw = data.get("watchlist")
    if raw is None:
        return Response({"ok": False, "error": "watchlist required"}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(raw, str):
        names = [x.strip().upper() for x in raw.split(",") if x.strip()]
    else:
        names = [str(x).upper().replace(".NS", "") for x in raw]
    names = list(dict.fromkeys(n for n in names if n in LOT_SIZE or n in {"NIFTY", "BANKNIFTY", "SENSEX"}))
    ps.fno_watchlist = names
    if "auto_analyze" in data:
        ps.fno_auto_analyze = bool(data.get("auto_analyze") in (True, "true", "1", 1, "yes", "on"))
    ps.save(update_fields=["fno_watchlist", "fno_auto_analyze", "updated_at"])
    return Response(
        {
            "ok": True,
            "watchlist": ps.fno_watchlist,
            "auto_analyze": ps.fno_auto_analyze,
            "catalog": catalog,
        }
    )


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def agents_settings(request):
    """GET/POST TradingAgents glue settings (stored on PortfolioSettings.agents_config)."""
    from research.adapter import load_agents_settings

    ps = PortfolioSettings.get_solo()
    if request.method == "GET":
        merged = load_agents_settings()
        return Response({"ok": True, "settings": merged, "stored": ps.agents_config or {}})

    data = request.data or {}
    allowed = {
        "enabled",
        "top_n",
        "daily_call_cap",
        "daily_cost_cap_inr",
        "timeout_sec",
        "retry_budget",
        "max_debate_rounds",
        "max_risk_rounds",
        "selected_analysts",
        "checkpoint_enabled",
        "llm_provider",
        "quick_think_llm",
        "deep_think_llm",
    }
    stored = dict(ps.agents_config or {})
    for k in allowed:
        if k in data:
            stored[k] = data[k]
    # Cost warning gate: enabling requires acknowledge_cost=true
    if stored.get("enabled") in (True, "true", "1", 1, "yes", "on"):
        if not data.get("acknowledge_cost"):
            return Response(
                {
                    "ok": False,
                    "error": (
                        "Enabling agents requires acknowledge_cost=true. "
                        "A full multi-agent run can cost multiple LLM calls per symbol."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        stored["enabled"] = True
    elif "enabled" in data:
        stored["enabled"] = False
    ps.agents_config = stored
    ps.save(update_fields=["agents_config", "updated_at"])
    return Response({"ok": True, "settings": load_agents_settings(), "stored": stored})


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def models_settings(request):
    """GET/POST Kronos + consensus settings (PortfolioSettings.models_config)."""
    from forecast.adapter import load_kronos_settings

    ps = PortfolioSettings.get_solo()
    if request.method == "GET":
        return Response(
            {
                "ok": True,
                "kronos": load_kronos_settings(),
                "stored": ps.models_config or {},
                "weights": getattr(cfg, "CONSENSUS_WEIGHTS", {}),
            }
        )

    data = request.data or {}
    allowed = {
        "kronos_enabled",
        "kronos_model_size",
        "kronos_paths",
        "consensus_veto",
        "consensus_enabled",
    }
    stored = dict(ps.models_config or {})
    for k in allowed:
        if k in data:
            stored[k] = data[k]
    if "kronos_enabled" in stored:
        stored["kronos_enabled"] = stored["kronos_enabled"] in (
            True,
            "true",
            "1",
            1,
            "yes",
            "on",
        )
    if "consensus_veto" in stored:
        stored["consensus_veto"] = stored["consensus_veto"] in (
            True,
            "true",
            "1",
            1,
            "yes",
            "on",
        )
    ps.models_config = stored
    ps.save(update_fields=["models_config", "updated_at"])
    return Response(
        {
            "ok": True,
            "kronos": load_kronos_settings(),
            "stored": stored,
            "weights": getattr(cfg, "CONSENSUS_WEIGHTS", {}),
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def calls_today(request):
    """JSON board of today's model calls + consensus."""
    from django.utils import timezone

    from signals.consensus import consensus_from_signal

    today = timezone.localdate()
    qs = (
        Signal.objects.filter(created_at__date=today)
        .select_related("instrument")
        .order_by("-probability")[:50]
    )
    rows = []
    for s in qs:
        pipe = s.pipeline or {}
        bundle = pipe.get("consensus") and pipe.get("model_calls")
        if not bundle:
            try:
                fresh = consensus_from_signal(s)
                models = fresh["models"]
                consensus = fresh["consensus"]
            except Exception:  # noqa: BLE001
                models, consensus = {}, {}
        else:
            models = pipe.get("model_calls") or {}
            consensus = pipe.get("consensus") or {}
        rows.append(
            {
                "id": s.id,
                "symbol": s.instrument.symbol,
                "side": s.side,
                "status": s.status,
                "gate": (s.gate or {}).get("result"),
                "probability": s.probability,
                "models": {
                    k: {
                        "call": v.get("call"),
                        "vote": v.get("vote"),
                        "parameters": v.get("parameters"),
                        "unavailable": v.get("unavailable"),
                    }
                    for k, v in models.items()
                    if isinstance(v, dict) and k != "skill_checklist"
                },
                "skill_checklist": models.get("skill_checklist")
                or pipe.get("skill_checklist")
                or [],
                "consensus": consensus,
            }
        )
    return Response({"ok": True, "as_of": today.isoformat(), "count": len(rows), "calls": rows})


@api_view(["POST"])
@permission_classes([AllowAny])
def calls_refresh(request):
    """Enqueue background model review for today's signals."""
    from django.utils import timezone

    from signals.model_review import enqueue_model_review

    today = timezone.localdate()
    ids = list(
        Signal.objects.filter(created_at__date=today).values_list("id", flat=True)[:80]
    )
    enqueue_model_review(ids)
    return Response({"ok": True, "count": len(ids)})


@api_view(["POST"])
@permission_classes([AllowAny])
def research_run(request):
    """Enqueue (or sync) a ResearchRun for a symbol — Advisor / manual trigger."""
    from research.services import run_analysis

    symbol = (request.data.get("symbol") or "").strip()
    if not symbol:
        return Response({"ok": False, "error": "symbol required"}, status=status.HTTP_400_BAD_REQUEST)
    as_of = (request.data.get("as_of") or "").strip() or None
    # Default async — never block the web request
    sync = bool(request.data.get("sync") in (True, "true", "1", 1))
    fno = bool(request.data.get("fno_underlying") in (True, "true", "1", 1))
    signal_id = request.data.get("signal_id")
    signal = None
    if signal_id:
        signal = Signal.objects.filter(pk=signal_id).first()
    run = run_analysis(
        symbol,
        as_of=as_of,
        signal=signal,
        fno_underlying=fno,
        sync=sync,
    )
    return Response(
        {
            "ok": True,
            "id": run.id,
            "status": run.status,
            "rating": run.rating,
            "yahoo_ticker": run.yahoo_ticker,
            "error": run.error,
            "report_url": f"/research/report/{run.id}/" if run.report_html_path else None,
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def research_runs(request):
    from research.models import ResearchRun

    qs = ResearchRun.objects.all()[:50]
    rows = [
        {
            "id": r.id,
            "symbol": r.symbol,
            "yahoo_ticker": r.yahoo_ticker,
            "as_of": r.as_of.isoformat(),
            "status": r.status,
            "rating": r.rating,
            "error": r.error[:200] if r.error else "",
            "report_url": f"/research/report/{r.id}/" if r.report_html_path else None,
        }
        for r in qs
    ]
    return Response({"ok": True, "runs": rows})


@api_view(["POST"])
@permission_classes([AllowAny])
def reset_life(request):
    """Clear DEAD / restore wallet from Settings (local single-user)."""
    equity = request.data.get("equity")
    cash = request.data.get("cash")
    try:
        ps = reset_life_status(
            equity=float(equity) if equity is not None else None,
            cash=float(cash) if cash is not None else None,
        )
    except ValueError as exc:
        return Response({"ok": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        {
            "ok": True,
            "life_status": ps.life_status,
            "equity": float(ps.equity),
            "cash": float(ps.cash),
            "kill_switch": ps.kill_switch,
        }
    )


def positions_stream(request):
    """SSE stream of live position marks (CPU-light shared hub).

    Prefer this over polling /api/positions/live/. EventSource auto-reconnects.
    """
    import json

    from django.http import StreamingHttpResponse

    from portfolio.live_hub import get_hub

    hub = get_hub()
    hub.add_subscriber()

    def event_stream():
        last_fp = ""
        try:
            snap, last_fp = hub.latest()
            if snap is None:
                snap = hub.force_refresh()
                _, last_fp = hub.latest()
            yield f"data: {json.dumps(snap, default=str)}\n\n"
            while True:
                snap, fp = hub.wait_update(last_fp, timeout=12.0)
                if snap is None or fp == last_fp:
                    yield ": ping\n\n"
                    continue
                last_fp = fp
                yield f"data: {json.dumps(snap, default=str)}\n\n"
        except GeneratorExit:
            pass
        finally:
            hub.remove_subscriber()

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache, no-store"
    resp["X-Accel-Buffering"] = "no"
    resp["Connection"] = "keep-alive"
    return resp
