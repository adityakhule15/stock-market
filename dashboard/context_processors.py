from datetime import datetime
from zoneinfo import ZoneInfo

from config import trading_config as cfg
from portfolio.models import PortfolioSettings
from portfolio.services import paper_gates_passed, refresh_life_status

IST = ZoneInfo("Asia/Kolkata")


def survival_banner(request):
    try:
        ps = refresh_life_status(PortfolioSettings.get_solo(), alert=False)
        equity = float(ps.equity)
        cash = float(ps.cash)
        start = float(ps.start_equity or cfg.CAPITAL)
        wallet = min(equity, cash, float(cfg.CAPITAL))
        pct_start = (equity / start * 100.0) if start else 0.0
        dist_survival = equity - float(cfg.SURVIVAL_EQUITY)
        dist_dead = equity - float(cfg.DEAD_FLOOR)
        lockout_until = ps.lockout_until
        lockout_active = False
        lockout_countdown = ""
        if lockout_until:
            now = datetime.now(IST)
            until = lockout_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=IST)
            else:
                until = until.astimezone(IST)
            if until > now:
                lockout_active = True
                secs = int((until - now).total_seconds())
                h, rem = divmod(secs, 3600)
                m, _ = divmod(rem, 60)
                lockout_countdown = f"{h}h {m}m"

        regime = {}
        try:
            from marketdata.regime import detect_regime

            regime = detect_regime()
        except Exception:  # noqa: BLE001
            regime = {"tag": "range", "reason": "unavailable"}

        events = {}
        try:
            from marketdata.events import event_flags

            events = event_flags()
        except Exception:  # noqa: BLE001
            events = {}

        caution = equity <= float(getattr(cfg, "CAUTION_EQUITY", cfg.SURVIVAL_EQUITY))
        drawdown_pct = max(0.0, (1.0 - equity / start) * 100.0) if start else 0.0
        fno_paused = equity < float(getattr(cfg, "FNO_PAUSE_BELOW_EQUITY", 0) or 0)
        protect_msg = ""
        if ps.life_status == "dead":
            protect_msg = "Stop trading. Review journal mistakes — do not force new trades."
        elif ps.life_status == "survival":
            protect_msg = (
                "Capital defence mode: max 1 idea, higher bar — sit out unless edge is clear. "
                "Goal is stop the bleed, not win it back today."
            )
        elif caution:
            protect_msg = (
                f"Drawdown {drawdown_pct:.0f}% from start — protect capital. "
                "Skip weak tickets; no revenge re-entries; F&O paused until recovery."
                if fno_paused
                else (
                    f"Drawdown {drawdown_pct:.0f}% from start — protect capital. "
                    "Fewer trades, higher probability only. Do not stack losses."
                )
            )

        from dashboard.plain_language import distance_words, what_you_can_do_today
        from portfolio.strategy_gates import strategy_unlocked

        fno_unlocked = strategy_unlocked("fno")
        force_paper = cfg.FORCE_PAPER_UNTIL_GATES and not paper_gates_passed()
        what = what_you_can_do_today(
            life_status=ps.life_status,
            kill_switch=bool(ps.kill_switch),
            lockout=lockout_active,
            fno_unlocked=fno_unlocked,
            force_paper=force_paper,
        )
        return {
            "life_status": ps.life_status,
            "life_equity": ps.equity,
            "life_cash": ps.cash,
            "wallet_amount": round(wallet, 2),
            "wallet_cap": cfg.CAPITAL,
            "wallet_start": start,
            "wallet_pct_start": round(pct_start, 1),
            "dist_survival": round(dist_survival, 2),
            "dist_dead": round(dist_dead, 2),
            "dist_survival_words": distance_words(dist_survival, "Survival"),
            "dist_dead_words": distance_words(dist_dead, "Stop-trading"),
            "dead_floor": cfg.DEAD_FLOOR,
            "survival_equity": cfg.SURVIVAL_EQUITY,
            "force_paper": force_paper,
            "kill_switch_on": ps.kill_switch,
            "lockout_active": lockout_active,
            "lockout_until": lockout_until,
            "lockout_countdown": lockout_countdown,
            "consecutive_losses": int(ps.consecutive_losses or 0),
            "market_regime": regime,
            "event_flags": events,
            "mistake_tags": cfg.MISTAKE_TAGS,
            "exit_reasons": cfg.EXIT_REASONS,
            "capital_caution": caution,
            "drawdown_pct": round(drawdown_pct, 1),
            "fno_paused": fno_paused,
            "fno_strategy_unlocked": fno_unlocked,
            "protect_msg": protect_msg,
            "what_you_can_do": what or protect_msg,
        }
    except Exception:  # noqa: BLE001
        return {
            "life_status": "alive",
            "life_equity": cfg.CAPITAL,
            "life_cash": cfg.CAPITAL,
            "wallet_amount": cfg.CAPITAL,
            "wallet_cap": cfg.CAPITAL,
            "wallet_start": cfg.CAPITAL,
            "wallet_pct_start": 100.0,
            "dist_survival": cfg.CAPITAL - cfg.SURVIVAL_EQUITY,
            "dist_dead": cfg.CAPITAL - cfg.DEAD_FLOOR,
            "dead_floor": cfg.DEAD_FLOOR,
            "survival_equity": cfg.SURVIVAL_EQUITY,
            "force_paper": True,
            "kill_switch_on": False,
            "lockout_active": False,
            "lockout_until": None,
            "lockout_countdown": "",
            "consecutive_losses": 0,
            "market_regime": {"tag": "range"},
            "event_flags": {},
            "mistake_tags": cfg.MISTAKE_TAGS,
            "exit_reasons": cfg.EXIT_REASONS,
            "capital_caution": False,
            "drawdown_pct": 0.0,
            "fno_paused": False,
            "protect_msg": "",
        }
