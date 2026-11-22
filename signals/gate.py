from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

from config import trading_config as cfg

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class GateResult:
    passed: bool
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"result": "PASS" if self.passed else "BLOCK", "blocked_by": self.reasons}


@dataclass
class PortfolioState:
    kill_switch: bool = False
    day_pnl: float = 0.0
    week_pnl: float = 0.0
    trades_today: int = 0
    open_positions: int = 0
    free_cash: float = cfg.CAPITAL
    avg_volumes: dict = field(default_factory=dict)
    equity: float = cfg.CAPITAL
    life_status: str = "alive"
    risk_per_trade: float = cfg.RISK_PER_TRADE
    max_position_pct: float = cfg.MAX_POSITION_PCT
    max_open_positions: int = cfg.MAX_OPEN_POSITIONS
    max_trades_per_day: int = cfg.MAX_TRADES_PER_DAY
    min_prob: float = cfg.MIN_PROB
    lockout_until: datetime | None = None
    revenge_symbols: set = field(default_factory=set)
    consecutive_losses: int = 0

    def avg_volume(self, symbol: str) -> float:
        return float(self.avg_volumes.get(symbol, 1e9))


def in_entry_window(order: dict, now: datetime) -> bool:
    now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    t = now.time()
    strategy = order.get("strategy", "swing")
    # Overnight / weekend: planning tickets are OK (place next session)
    if not _is_weekday_session(now):
        return True
    if strategy in {"intraday", "fno_intraday"}:
        # Same-day F&O: enter after open noise, cancel if not filled by 14:00
        return time(9, 46) <= t <= time(14, 0)
    # Swing: pre-market plan through mid-afternoon
    return time(8, 45) <= t <= time(14, 30)


def _is_weekday_session(now: datetime) -> bool:
    return now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 30)


def risk_gate(
    o: dict,
    pf: PortfolioState,
    tm: dict,
    data_age_min: int,
    cfg_mod=cfg,
    now: datetime | None = None,
) -> GateResult:
    now = now or datetime.now(IST)
    why: list[str] = []
    session_open = _is_weekday_session(now)

    if pf.life_status == "dead":
        why.append("portfolio DEAD — equity at/below floor; staff reset required")
        return GateResult(False, why)

    if pf.kill_switch:
        why.append("kill switch is ON")

    if pf.lockout_until is not None:
        until = pf.lockout_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=IST)
        if now < until.astimezone(IST):
            why.append(
                f"cooling-off lockout until {until.astimezone(IST).strftime('%Y-%m-%d %H:%M')} IST"
            )

    max_loss_limit = pf.equity * pf.risk_per_trade if pf.risk_per_trade > 0 else 0.0
    # Also respect absolute config capital-based cap for ALIVE baseline
    max_loss_limit = min(max_loss_limit, cfg_mod.CAPITAL * max(pf.risk_per_trade, cfg_mod.RISK_PER_TRADE))
    if pf.life_status == "survival":
        max_loss_limit = pf.equity * pf.risk_per_trade

    if o["max_loss"] > max_loss_limit + 1e-9:
        why.append("max loss above per-trade limit (≤1.5% of equity)")

    # Existential floor: this trade must not be able to kill you
    if pf.equity - float(o["max_loss"]) < cfg_mod.DEAD_FLOOR - 1e-9:
        why.append("trade would breach DEAD_FLOOR")

    # Never breach the next life floor (ALIVE→SURVIVAL or SURVIVAL→DEAD)
    next_floor = (
        cfg_mod.DEAD_FLOOR if pf.equity <= cfg_mod.SURVIVAL_EQUITY else cfg_mod.SURVIVAL_EQUITY
    )
    if pf.equity > next_floor and pf.equity - float(o["max_loss"]) < next_floor - getattr(
        cfg_mod, "MAX_LOSS_FLOOR_BUFFER", 1.0
    ):
        why.append(f"trade would breach next floor ₹{next_floor:.0f}")

    if pf.day_pnl <= -cfg_mod.CAPITAL * cfg_mod.DAILY_LOSS_CAP:
        why.append("daily loss cap reached")
    if pf.week_pnl <= -cfg_mod.CAPITAL * cfg_mod.WEEKLY_LOSS_CAP:
        why.append("weekly loss cap reached")
    if pf.trades_today >= pf.max_trades_per_day:
        why.append("too many trades today")
    if pf.open_positions >= pf.max_open_positions:
        why.append("max open positions reached")

    pos_cap = min(pf.equity * pf.max_position_pct, pf.free_cash)
    if o["capital_used"] > pos_cap + 1e-9:
        why.append("position too big for limit or cash")
    if o["rr_net"] < cfg_mod.MIN_RR:
        why.append("net reward:risk below minimum")
    # Stale feed only matters while the market is open
    if session_open and data_age_min * 60 > cfg_mod.MAX_DATA_AGE_SEC:
        why.append("data too old — refresh feed before acting")
    # F&O local TM uses NEUTRAL/WEAK/EDGE — only treat equity COIN_FLIP as hard skip here
    if tm.get("verdict") == "COIN_FLIP":
        why.append("COIN FLIP — skip")
    elif tm.get("mode") == "fno_local":
        if tm.get("verdict") == "WEAK":
            why.append("F&O local edge weak")
    elif tm.get("verdict") != "EDGE":
        why.append("Time Machine: " + str(tm.get("verdict", "UNKNOWN")))
    if float(o.get("probability", 1.0)) < pf.min_prob:
        why.append("probability below mode minimum")
    symbol = o.get("symbol", "")
    bare = str(symbol).replace(".NS", "").replace(".BO", "").upper()
    revenge = {str(s).replace(".NS", "").replace(".BO", "").upper() for s in (pf.revenge_symbols or set())}
    if bare and bare in revenge:
        why.append("revenge block: same symbol hit SL today — no re-entry")
    if o["qty"] > 0.01 * pf.avg_volume(symbol):
        why.append("order too large for liquidity")
    if not in_entry_window(o, now):
        why.append("outside entry window")
    if pf.life_status == "survival" and pf.trades_today >= 1:
        why.append("survival mode: already traded today — top-1 only")

    # Meta filter: learn from past TradeReview mistakes (soft block when champion exists)
    try:
        from ml.meta_filter import predict_meta_win_prob

        meta_p = predict_meta_win_prob(o, regime=o.get("regime") if isinstance(o.get("regime"), dict) else None)
        if meta_p is not None:
            o["meta_win_prob"] = round(float(meta_p), 4)
            min_meta = float(getattr(cfg_mod, "META_MIN_WIN_PROB", 0.42))
            if meta_p < min_meta:
                why.append(
                    f"meta filter P(win)={meta_p:.2f} < {min_meta:.2f} — learned from past mistakes"
                )
    except Exception:  # noqa: BLE001
        pass

    return GateResult(not why, why)
