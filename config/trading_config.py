"""Capital protection and trading parameters for ₹2,000 capital.

Survival rule: if equity hits DEAD_FLOOR you are done until a manual reset.
Never size a trade that can push equity under that floor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeSchedule:
    STT_DELIVERY: float = 0.001  # 0.1% on buy+sell CNC
    STT_INTRADAY: float = 0.00025  # 0.025% on sell MIS
    EXCHANGE_TXN: float = 0.0000325
    SEBI: float = 0.000001
    STAMP_DELIVERY: float = 0.00015
    STAMP_INTRADAY: float = 0.00003
    GST: float = 0.18
    BROKERAGE_FLAT: float = 20.0  # flat per side; override via brokerage()

    def brokerage(self, value: float, product: str) -> float:
        # Discount broker style: min(flat, turnover fee) per side
        return min(self.BROKERAGE_FLAT, 0.0003 * value)


@dataclass(frozen=True)
class RiskParams:
    life_status: str
    risk_per_trade: float
    max_position_pct: float
    max_open_positions: int
    max_trades_per_day: int
    min_prob: float
    daily_loss_cap: float
    weekly_loss_cap: float


CAPITAL = 2000
DEAD_FLOOR = 800  # equity <= this → DEAD (manual reset only)
SURVIVAL_EQUITY = 1700  # between DEAD_FLOOR and this → SURVIVAL
KILL_EQUITY = 1500  # mid kill; arms kill switch
# Soft caution band: still ALIVE but raise bars after drawdown from start
CAUTION_EQUITY = 1850  # equity below this → caution risk (higher min_prob)

# ALIVE defaults (tightened for ₹2,000 survival after ~14% drawdown)
RISK_PER_TRADE = 0.01  # ₹20 max loss per trade (was 1.5%)
MAX_POSITION_PCT = 0.30  # ₹600 max per position
MAX_OPEN_POSITIONS = 1
DAILY_LOSS_CAP = 0.03  # ₹60
WEEKLY_LOSS_CAP = 0.05  # ₹100
MIN_PROB = 0.55  # stricter LightGBM gate — reject coin-flip tickets
CAUTION_MIN_PROB = 0.58
MIN_RR = 1.6  # net-of-cost
ATR_SL_MULT = 1.5
T1_R, T2_R = 2.0, 3.0
ENTRY_BUFFER, SL_BUFFER = 0.003, 0.003
PRODUCT = "CNC"
NO_LEVERAGE = True
MAX_TRADES_PER_DAY = 1
MAX_DATA_AGE_SEC = 1200  # 15 min delay + 5 min slack
TM_SIMS, TM_SEED, TM_MIN_EDGE_R = 10_000, 19, 0.20  # demand clearer edge
INTRADAY_ENABLED = True
ORB_MIN_WIDTH, ORB_MAX_WIDTH, ORB_VOL_MULT = 0.004, 0.02, 1.5
FORCE_PAPER_UNTIL_GATES = True
# After capital damage: prefer CNC / skip weak F&O until recovery
FNO_MIN_PROB = 0.58
FNO_PAUSE_BELOW_EQUITY = 1800  # block new F&O suggestions when equity below this

# HARD RULE: StockScope never places broker orders (Groww / INDstocks / any API).
# Signals + wallet sizing + optional live LTP only. You place orders yourself in the broker app.
ALLOW_BROKER_ORDERS = False
INDSTOCKS_DATA_ONLY = True

# SURVIVAL overrides
SURVIVAL_RISK_PER_TRADE = 0.008  # ₹16
SURVIVAL_MIN_PROB = 0.60
SURVIVAL_MAX_POSITION_PCT = 0.25

# Wallet lockout / floor protection
COOLING_OFF_LOSSES = 2
COOLING_OFF_HOURS = 24
MAX_LOSS_FLOOR_BUFFER = 1.0  # ₹ slack when checking next life floor
RESULTS_WARN_DAYS = 2  # results within N days → warn CNC / block F&O
RETRAIN_MIN_SAMPLES = 30
# Soft gate: block NEW signals when meta P(win) is below this (only if champion exists)
META_MIN_WIN_PROB = 0.42
ALIVE_TOP_N = 3
SURVIVAL_TOP_N = 1
SQUARE_OFF_BY = "15:10"
SQUARE_OFF_WARN_MIN = 10

# Open-position adverse exit (advisor / management) — do not HOLD into deep losses
# Options: exit when adverse R hits this, or premium falls this fraction from entry
ADVERSE_R_EXIT = -0.50
FNO_PREMIUM_DROP_EXIT = 0.20  # 20% premium decay from entry → EXIT_NOW
# Soft cut: model votes SELL when progress this negative (fraction of entry→target)
ADVERSE_PROGRESS_SELL = -0.25
FNO_PNL_PCT_SELL = -15.0  # gross open P&L % of invested

# India VIX / NIFTY regime thresholds
VIX_HIGH = 18.0
VIX_LOW = 12.0
NIFTY_RANGE_ATR_MULT = 0.6  # |day move| / ATR below this → range

# Mistake tags for TradeReview (fixed list)
MISTAKE_TAGS = (
    "entered_early",
    "entered_late",
    "chased",
    "ignored_sl",
    "moved_sl_wider",
    "exited_early",
    "oversized",
    "ignored_news",
    "revenge_trade",
    "followed_signal_correctly",
)

EXIT_REASONS = (
    "target",
    "sl",
    "manual_early",
    "manual_late",
    "time",
)

FEES = FeeSchedule()

# Universe filters
MIN_PRICE = 10.0
MAX_PRICE = 1000.0
MIN_ADV_CR = 10.0  # ₹10 cr 20-day average traded value
MAX_POSITION_FRACTION_OF_ADV = 0.01

# Model / retrain
MIN_PROB_EXIT = 0.45
ROLLBACK_HIT_RATE = 0.35
ROLLBACK_LOOKBACK_SIGNALS = 20
PSI_THRESHOLD = 0.2

# Backtest
SLIPPAGE_PCT = 0.00075

# Paper go-live gates (§17)
PAPER_MIN_SIGNALS = 30
PAPER_MIN_EXPECTANCY = 0.0

# ---------------------------------------------------------------------------
# Research agents (TradingAgents) — glue only; default OFF until backtest evidence
# ---------------------------------------------------------------------------
AGENTS_ENABLED = False
AGENTS_TOP_N = 3
AGENTS_DAILY_CALL_CAP = 6
AGENTS_DAILY_COST_CAP_INR = 0.0  # 0 = disabled
AGENTS_RUN_TIMEOUT_SEC = 600
AGENTS_RETRY_BUDGET = 1
AGENTS_MAX_DEBATE_ROUNDS = 1
AGENTS_MAX_RISK_ROUNDS = 1
AGENTS_SELECTED_ANALYSTS = ("market", "social", "news", "fundamentals")
AGENTS_CHECKPOINT_ENABLED = True
AGENTS_HOLDING_PERIOD_DAYS = 5
AGENTS_RANK_BOOST_BUY = 4.0  # mild score bump for Buy/Overweight
AGENTS_RANK_PENALTY_HOLD = 8.0  # lower rank when agents_neutral


def life_status_for_equity(equity: float) -> str:
    if equity <= DEAD_FLOOR:
        return "dead"
    if equity <= SURVIVAL_EQUITY:
        return "survival"
    return "alive"


def risk_params_for_equity(equity: float) -> RiskParams:
    status = life_status_for_equity(equity)
    if status == "dead":
        return RiskParams(
            life_status="dead",
            risk_per_trade=0.0,
            max_position_pct=0.0,
            max_open_positions=0,
            max_trades_per_day=0,
            min_prob=1.0,
            daily_loss_cap=DAILY_LOSS_CAP,
            weekly_loss_cap=WEEKLY_LOSS_CAP,
        )
    if status == "survival":
        return RiskParams(
            life_status="survival",
            risk_per_trade=SURVIVAL_RISK_PER_TRADE,
            max_position_pct=SURVIVAL_MAX_POSITION_PCT,
            max_open_positions=1,
            max_trades_per_day=1,
            min_prob=SURVIVAL_MIN_PROB,
            daily_loss_cap=DAILY_LOSS_CAP,
            weekly_loss_cap=WEEKLY_LOSS_CAP,
        )
    # Caution band: still ALIVE but after drawdown — stricter probability
    min_prob = CAUTION_MIN_PROB if equity <= CAUTION_EQUITY else MIN_PROB
    risk = RISK_PER_TRADE * (0.8 if equity <= CAUTION_EQUITY else 1.0)
    return RiskParams(
        life_status="alive",
        risk_per_trade=risk,
        max_position_pct=MAX_POSITION_PCT,
        max_open_positions=MAX_OPEN_POSITIONS,
        max_trades_per_day=MAX_TRADES_PER_DAY,
        min_prob=min_prob,
        daily_loss_cap=DAILY_LOSS_CAP,
        weekly_loss_cap=WEEKLY_LOSS_CAP,
    )
