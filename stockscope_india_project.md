# StockScope India — AI Stock Market Analyzer

> One Django project. Live/latest NSE data → features → ML model → **buy/sell order tickets with trigger price + stop-loss + target** → dashboard → **continuous retraining**.
> Built around a **₹2,000 trading capital**. Signals-only in v1 (you place the orders yourself). **No broker login or API keys needed.**

---

## 1. Requirements checklist

| # | Requirement | How this project meets it |
|---|---|---|
| 1 | Indian stock market | NSE equities, symbols like `TATASTEEL.NS`, tick size ₹0.05, IST session 09:15–15:30 |
| 2 | Real-time / latest data, **no broker account** | Free public sources only (`yfinance` primary, NSE end-of-day files, optional community NSE wrappers). Free NSE data is **≈15 min delayed**, so the app is built to work under that delay and shows data age on every signal (§4) |
| 3 | No MCP | Plain Python HTTP clients only. No MCP servers anywhere |
| 4 | Buy & sell orders with trigger + stop-loss | `OrderTicket` generator (§9) outputs entry trigger, limit, SL trigger, SL limit, targets, qty |
| 5 | ₹2,000 budget | Position sizing, 2% risk per trade, daily loss cap, kill switch (§13) |
| 6 | Django/Flask/FastAPI, templates inside, one project | **Django 5 + DRF + Django templates + HTMX**, one repo, one `manage.py` |
| 7 | API integrations | yfinance, NSE end-of-day files, RSS news, Telegram alerts (§4) |
| 8 | Model training + continuous retraining | Nightly incremental, weekly full retrain, drift-triggered retrain, champion/challenger promotion (§8) |
| 9 | Time Machine | 10,000-path simulation + historical replay of the same setup before every trade (§10) |
| 10 | Opening-range-breakout scanner | Optional intraday mode, off by default, delay-aware pending-order plans (§11) |
| 11 | PASS / BLOCK risk gate | Every order is checked against your limits; blocked orders show the reason (§12) |

---

## 2. Reality check (read before using real money)

- **No model can guarantee zero loss.** Markets are noisy and price-only ML has a small edge at best. This project can only *bound* your loss (hard caps in §13), not remove it.
- **Free, no-account NSE data is about 15 minutes delayed** (Yahoo Finance, free TradingView and most free APIs). True real-time NSE ticks are licensed by the exchange and normally come through a broker or a paid vendor. That is why swing trading is the default and your own broker app does the live triggering (§4.5).
- Brokerage/taxes/slippage are a big drag on ₹2,000. The system only emits a trade if the **reward after estimated costs is still ≥ 2× the risk**.
- **Paper trade first** (gate in §17). Go live only after the paper results pass.
- v1 is **signal-only**. Auto-placing orders through a broker API falls under SEBI/broker retail-algo rules — check your broker's current requirements before adding execution.
- This is a personal decision-support tool. Sharing signals with other people as recommendations may require SEBI registration (Research Analyst / Investment Adviser).

---

## 3. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Web | Django 5.x, Django REST Framework, Django templates, HTMX, TradingView `lightweight-charts` (CDN) |
| DB | SQLite (dev) → PostgreSQL (prod) |
| Scheduler | APScheduler running in a `run_worker` management command (no Redis needed) |
| Data | `pandas`, `numpy`, `pyarrow` (parquet cache) |
| Indicators | `pandas-ta` (or `ta`) |
| ML | `lightgbm`, `scikit-learn`, `optuna`, `joblib`; optional `shap` |
| Market data | `yfinance` (primary, no login), NSE end-of-day files, optional community NSE wrappers (`jugaad-data` / `nsepython`), `requests` |
| News (optional) | `feedparser` + FinBERT (`transformers`) |
| Alerts | Telegram Bot API via `requests` |
| Tests | `pytest`, `pytest-django` |

---

## 4. Data layer — live/latest data from public sources, **no broker account**

Your broker login and keys are never used or needed. The app only *reads* public market data; you place the orders yourself in your own broker app.

> **Honest limit:** real-time NSE ticks are licensed by the exchange and normally reach you through a broker or a paid vendor. Every free, no-account source (Yahoo Finance / `yfinance`, free TradingView, most free APIs) serves NSE about **15 minutes delayed**. The app is built to be correct under that delay (§4.5) and shows the data age on every signal.

### 4.1 Sources (priority order, no login, no keys)

| Priority | Source | Use | Notes |
|---|---|---|---|
| 1 | `yfinance` / Yahoo chart data (`RELIANCE.NS`, `^NSEI`, `^INDIAVIX`) | Daily history, 5-min bars (last ~60 days), latest bar, index + VIX | Free, ~15-min delayed for NSE, unofficial: can lag, change or rate-limit |
| 2 | NSE's published end-of-day files (bhavcopy, index data) | Backfill, close verification, delivery data | Free, official, end-of-day |
| 3 | Community NSE wrappers (`jugaad-data`, `nsepython`) | Optional second opinion on quotes, Nifty 200 list | Read NSE's public website: fragile and subject to NSE's terms. Off by default, personal use only |
| 4 | RSS feeds (Moneycontrol, ET Markets) | Optional news-sentiment feature | Headlines only |
| Slot | `LiveFeedProvider` (optional, later) | Drop-in real-time feed if you ever add your own | Any keys stay on *your* machine in `.env`; nothing else in the app changes |

Free API-key vendors (Alpha Vantage, Twelve Data and similar) can be added as extra providers, but check that their free tier actually covers NSE before relying on one.

### 4.2 Provider interface (swap sources without touching the rest)

```python
# marketdata/providers/base.py
from typing import Protocol
import pandas as pd

class DataProvider(Protocol):
    name: str
    delay_min: int                                              # 15 for yfinance, 0 for a live feed
    def history(self, symbol: str, interval: str, start=None, end=None, period=None) -> pd.DataFrame: ...
    def latest_bar(self, symbol: str) -> dict: ...              # {"ts","o","h","l","c","v"}
```

`marketdata/service.py` tries providers in priority order and records which one answered (`Bar.source`) and its delay.

### 4.3 Polling with rate-limit discipline

```python
# marketdata/providers/yfinance_provider.py
import time, random
import yfinance as yf

class YFinanceProvider:
    name, delay_min = "yfinance", 15

    def history_many(self, symbols, interval="5m", period="5d"):
        for attempt in range(4):
            try:
                df = yf.download(" ".join(symbols), interval=interval, period=period,
                                 group_by="ticker", auto_adjust=False, progress=False, threads=True)
                if not df.empty:
                    return df
            except Exception:
                pass
            time.sleep(2 ** attempt + random.random())          # exponential backoff + jitter
        raise ProviderDown(self.name)
```

- Download in batches of ≤ 50 symbols, cache every result in parquet, refresh the full universe once a day and only the shortlist + open signals every 5 minutes.
- Interval limits: 5-min bars go back about 60 days, 1-min about 7 days. The Time Machine (§10) is built around these.
- If Yahoo returns empty or stale data → fall back to NSE end-of-day files → record `data_source`. If nothing is fresh enough, the signal is **blocked**, never guessed.

### 4.4 Staleness guard

```python
# marketdata/freshness.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")
MAX_AGE_SEC = {"yfinance": 1200, "nse_wrapper": 1200, "live_optional": 90}   # 15-min delay + 5-min slack

class SignalBlocked(Exception): ...

def assert_fresh(bar_ts: datetime, source: str, now: datetime | None = None) -> timedelta:
    now = now or datetime.now(IST)
    age = now - bar_ts
    if is_market_open(now) and age > timedelta(seconds=MAX_AGE_SEC[source]):
        raise SignalBlocked(f"{source} feed stalled: newest bar is {age} old")
    return age                                                  # shown as data_age_min on the ticket
```

The guard catches a **stalled feed** (newest bar older than delay + slack during market hours). It does not pretend delayed data is live: `data_age_min` is printed on every ticket.

### 4.5 Why a 15-minute delay is workable (delay-tolerant by design)

1. **Swing is the default.** Decisions come from completed daily bars, so a 15-minute delay changes nothing.
2. **Your broker does the live triggering.** You place the entry as a pending stop-limit order and the stop-loss order in your own broker app; the exchange-live feed fills them. The app only computes the levels.
3. **Chase protection.** Every ticket has `skip_if_live_price_above` (entry + 0.25R). Glance at the live price in your broker app before placing; if it is already past that level, skip.
4. **Intraday works as pending-order plans, not tick reactions** (§11), and is off by default.
5. **Market closed (08:45 IST pre-market plan):** uses the last completed session and sets `valid_until`.
6. **Raw vs adjusted prices:** use raw prices for trigger/SL levels; use adjusted history only for features and backtests. Check corporate actions (splits/bonuses).

### 4.6 Tradable universe for ₹2,000

Start from Nifty 200, then filter nightly:

- Price ₹10 – ₹1,000 (so one position ≤ 50% of capital buys ≥ 1 share)
- 20-day average traded value ≥ ₹10 cr (liquidity)
- Not in ASM/GSM lists; not in 5%/10% circuit-limit bands
- No results announcement within ±1 session (earnings gap risk)

---

## 5. Project structure

```text
stockscope/
├── manage.py
├── .env.example
├── requirements.txt
├── config/                      # Django project
│   ├── settings.py
│   ├── urls.py
│   └── trading_config.py        # capital, risk %, thresholds, fee rates
├── marketdata/
│   ├── models.py                # Instrument, Bar
│   ├── providers/               # base.py, yfinance_provider.py, nse_eod.py, nse_wrapper.py (optional)
│   ├── service.py               # failover chain
│   ├── freshness.py
│   ├── universe.py
│   └── management/commands/     # backfill.py, run_worker.py
├── features/
│   ├── indicators.py
│   ├── builders.py              # build_dataset(), no look-ahead
│   └── labels.py                # triple-barrier labels
├── ml/
│   ├── models.py                # ModelVersion, DriftReport
│   ├── train.py
│   ├── evaluate.py              # walk-forward, net-of-cost metrics
│   ├── registry.py              # save/load/promote/rollback
│   ├── drift.py
│   └── retrain.py
├── signals/
│   ├── models.py                # Signal
│   ├── risk.py                  # tick rounding, sizing, fees
│   ├── engine.py                # build_order(), gates
│   ├── gate.py                  # PASS/BLOCK risk gate (§12)
│   └── tasks.py
├── portfolio/
│   ├── models.py                # Position, EquitySnapshot
│   └── services.py              # kill switch, daily loss cap
├── timemachine/
│   └── sim.py                   # Monte Carlo + historical replay (§10)
├── scanner/
│   └── orb.py                   # opening-range-breakout plans (§11, optional)
├── backtest/
│   └── engine.py                # realistic fills + costs
├── dashboard/
│   ├── views.py                 # HTMX partials
│   ├── api.py                   # DRF viewsets
│   ├── templates/dashboard/     # base.html, signals.html, _signal_card.html, positions.html, models.html, performance.html
│   └── static/
├── notifications/telegram.py
├── data/                        # parquet cache (gitignored)
├── artifacts/                   # trained models (gitignored)
└── tests/
```

---

## 6. Database models (core)

```python
# signals/models.py
from django.db import models

class Signal(models.Model):
    class Side(models.TextChoices):
        BUY = "BUY"; SELL = "SELL"
    class Status(models.TextChoices):
        NEW = "new"; TRIGGERED = "triggered"; EXPIRED = "expired"
        STOPPED = "stopped"; TARGET = "target"; CLOSED = "closed"; SKIPPED = "skipped"

    instrument = models.ForeignKey("marketdata.Instrument", on_delete=models.CASCADE)
    side = models.CharField(max_length=4, choices=Side.choices)
    strategy = models.CharField(max_length=20)               # swing | intraday
    product = models.CharField(max_length=4, default="CNC")  # CNC | MIS
    created_at = models.DateTimeField(auto_now_add=True)
    data_as_of = models.DateTimeField()
    data_source = models.CharField(max_length=20)
    valid_until = models.DateTimeField()

    entry_trigger = models.DecimalField(max_digits=10, decimal_places=2)
    entry_limit   = models.DecimalField(max_digits=10, decimal_places=2)
    sl_trigger    = models.DecimalField(max_digits=10, decimal_places=2)
    sl_limit      = models.DecimalField(max_digits=10, decimal_places=2)
    target1       = models.DecimalField(max_digits=10, decimal_places=2)
    target2       = models.DecimalField(max_digits=10, decimal_places=2)
    qty           = models.PositiveIntegerField()
    capital_used  = models.DecimalField(max_digits=10, decimal_places=2)
    max_loss      = models.DecimalField(max_digits=10, decimal_places=2)
    rr_net        = models.FloatField()
    probability   = models.FloatField()
    model_version = models.ForeignKey("ml.ModelVersion", on_delete=models.PROTECT)
    status        = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    chase_limit   = models.DecimalField(max_digits=10, decimal_places=2)   # skip if live price is beyond this
    data_age_min  = models.PositiveSmallIntegerField(default=0)
    time_machine  = models.JSONField(default=dict)           # p_target, p_stop, replay counts, verdict (§10)
    gate          = models.JSONField(default=dict)           # PASS/BLOCK + reasons (§12)
    reasons       = models.JSONField(default=dict)           # top features / SHAP

    class Meta:
        indexes = [models.Index(fields=["status", "-created_at"])]


# ml/models.py
class ModelVersion(models.Model):
    class Status(models.TextChoices):
        CANDIDATE = "candidate"; CHAMPION = "champion"; RETIRED = "retired"
    name = models.CharField(max_length=40)                   # e.g. swing_lgbm
    version = models.PositiveIntegerField()
    algo = models.CharField(max_length=30)
    params = models.JSONField()
    train_start = models.DateField(); train_end = models.DateField()
    metrics = models.JSONField()                             # auc, brier, expectancy_R, max_dd, n_signals
    artifact_path = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CANDIDATE)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ("name", "version")
```

Other tables: `Instrument`, `Bar(instrument, interval, ts, o, h, l, c, v, source)` (unique on instrument+interval+ts), `Position`, `EquitySnapshot`, `DriftReport`, `JobRun`.

---

## 7. Features and labels

**Features (all computed from data available at the signal time — shift by 1 bar, no look-ahead):**

- Returns: 1/3/5/10/20-day, gap %, overnight vs intraday return
- Trend: EMA 9/20/50/200 distance, ADX, slope of EMA20
- Momentum: RSI(14), MACD histogram, Stochastic, ROC
- Volatility: ATR(14)/price, Bollinger %B and bandwidth, 20-day realised vol
- Volume: volume z-score, OBV slope, VWAP deviation (intraday)
- Structure: distance from 20-day high/low, 52-week high proximity, inside-day/NR7 flags
- Market context: Nifty 50 return/trend, India VIX level + 5-day change, sector index relative strength
- Calendar: day-of-week, days to expiry, results-in-window flag
- Optional: news sentiment score (FinBERT on RSS headlines)

**Label — triple barrier (per bar):**

```python
# features/labels.py
def triple_barrier(df, atr_mult_sl=1.5, rr=2.0, horizon=5):
    """1 if +rr*R is hit before -1R within `horizon` bars, else 0. R = atr_mult_sl * ATR."""
```

This matches the order ticket exactly (entry → SL at 1R, target at 2R), so the model predicts the thing you actually trade.

**Modes:** `swing` (daily bars, CNC delivery, 2–5 day hold — **default for ₹2,000**, fewer trades = less cost drag) and `intraday` (5-min bars, MIS, square-off before 15:10 — optional, off by default, §11).

---

## 8. Model training and continuous retraining

### 8.1 Models

| Stage | Model | Purpose |
|---|---|---|
| Baseline | Logistic regression + rule score | Sanity floor — a fancy model must beat it |
| Main | **LightGBM classifier** → probability of hitting target before SL | Production model |
| Calibration | Isotonic regression on validation fold | Makes `0.62` actually mean ~62% |
| Optional | LSTM/GRU or ensemble average | Only if it beats LightGBM net of costs |

### 8.2 Validation (no cheating)

- **Purged walk-forward CV** with an embargo equal to the label horizon (prevents leakage from overlapping labels).
- Metrics are **net of costs and slippage**: expectancy in R, hit rate, profit factor, max drawdown, Brier score, AUC, precision@top-k.
- Never tune on the final test window.

### 8.3 Continuous retraining schedule

| Trigger | Job | Output |
|---|---|---|
| Every trading day 16:30 IST | Ingest closes → rebuild features → incremental refit (LightGBM `init_model`) on rolling 3-year window | New **candidate** |
| Every Saturday 10:00 IST | Full retrain + Optuna (≈30 trials) | New **candidate** |
| Drift alert (hourly check) | Immediate retrain | New **candidate** |
| After any candidate | Champion/challenger comparison (below) | Promote or discard |

### 8.4 Champion / challenger promotion

```python
# ml/retrain.py
def retrain_and_maybe_promote(name="swing_lgbm"):
    df = build_dataset(universe(), end=last_closed_session())
    candidate = fit_lgbm(df, params=best_params_or_default(name))
    cand_m = evaluate_walk_forward(candidate, df, last_n_sessions=60)   # net of costs
    champ = registry.champion(name)
    champ_m = evaluate_walk_forward(champ.load(), df, last_n_sessions=60) if champ else None

    promote = (
        champ_m is None or (
            cand_m["expectancy_R"] >= champ_m["expectancy_R"] + 0.05
            and cand_m["max_dd"] <= champ_m["max_dd"] * 1.10
            and cand_m["brier"] <= champ_m["brier"]
            and cand_m["n_signals"] >= 30
        )
    )
    mv = registry.save(candidate, metrics=cand_m, status="candidate")
    if promote:
        registry.promote(mv)          # old champion -> retired (kept for rollback)
    return mv, promote
```

**Auto-rollback:** if the live/paper hit rate over the last 20 signals falls below 35% (or expectancy < 0), the previous champion is restored and new signals pause until you review.

### 8.5 Drift detection (`ml/drift.py`)

- PSI > 0.2 on any of the top-10 features
- Rolling AUC/Brier worse than validation by a set margin
- Regime flag: India VIX jumps > 30% in 5 days

Results are written to `DriftReport` and shown on the Models page.

---

## 9. Signal engine and order tickets

### 9.1 Gates — a signal is emitted only if **all** pass

1. Data fresh (`assert_fresh`)
2. Model probability ≥ `MIN_PROB` (default 0.60, after calibration)
3. Trend filter: close > EMA50 for BUY (reverse for intraday SELL)
4. Market regime: Nifty above its EMA20 and India VIX below threshold for BUY
5. `qty ≥ 1` and `capital_used ≤ MAX_POSITION_PCT × capital`
6. **Net reward:risk after estimated costs ≥ 2.0**
7. Portfolio rules OK: open positions < 2, daily loss cap not hit, kill switch off (§13)
8. **Time Machine verdict = EDGE** (§10)
9. **Risk gate = PASS** (§12), re-run right before the ticket is shown

### 9.2 Tick rounding, sizing, fees

```python
# signals/risk.py
import math
TICK = 0.05

def round_tick(price: float, direction: str = "nearest") -> float:
    t = price / TICK
    t = math.ceil(t - 1e-9) if direction == "up" else math.floor(t + 1e-9) if direction == "down" else round(t)
    return round(t * TICK, 2)

def position_size(entry, stop, capital, risk_pct, max_pos_pct) -> int:
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return 0
    by_risk = math.floor(capital * risk_pct / risk_per_share)
    by_cap  = math.floor(capital * max_pos_pct / entry)
    return max(0, min(by_risk, by_cap))

def estimate_costs(buy_value, sell_value, product, f):
    """f = FEES from trading_config (STT, exchange txn, SEBI, stamp, GST, brokerage). Verify against your broker's charge sheet."""
    brokerage = f.brokerage(buy_value, product) + f.brokerage(sell_value, product)
    stt = (f.STT_DELIVERY * (buy_value + sell_value)) if product == "CNC" else f.STT_INTRADAY * sell_value
    exch = f.EXCHANGE_TXN * (buy_value + sell_value)
    sebi = f.SEBI * (buy_value + sell_value)
    stamp = (f.STAMP_DELIVERY if product == "CNC" else f.STAMP_INTRADAY) * buy_value
    gst = f.GST * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + stamp + gst
```

### 9.3 Building the order

```python
# signals/engine.py
from signals.risk import round_tick, position_size, estimate_costs, TICK

def build_order(side, ctx, prob, cfg):
    s = 1 if side == "BUY" else -1
    toward = "down" if s == 1 else "up"      # round targets toward entry (conservative)
    away   = "up" if s == 1 else "down"

    base = ctx.prev_high if s == 1 else ctx.prev_low
    trig = round_tick(base + s * 2 * TICK, away)                       # breakout trigger
    lim  = round_tick(trig * (1 + s * cfg.ENTRY_BUFFER), away)         # limit above/below trigger
    sl_t = round_tick(trig - s * cfg.ATR_SL_MULT * ctx.atr, toward if s == -1 else "down")
    sl_l = round_tick(sl_t * (1 - s * cfg.SL_BUFFER), "down" if s == 1 else "up")
    R    = abs(trig - sl_t)
    t1   = round_tick(trig + s * cfg.T1_R * R, toward)
    t2   = round_tick(trig + s * cfg.T2_R * R, toward)

    qty = position_size(trig, sl_t, cfg.CAPITAL, cfg.RISK_PER_TRADE, cfg.MAX_POSITION_PCT)
    if qty < 1:
        return None
    costs = estimate_costs(trig * qty, t1 * qty, cfg.PRODUCT, cfg.FEES)
    rr_net = (abs(t1 - trig) * qty - costs) / (R * qty + costs)
    if rr_net < cfg.MIN_RR or prob < cfg.MIN_PROB:
        return None
    return dict(side=side, entry_trigger=trig, entry_limit=lim, chase_limit=round_tick(trig + s * 0.25 * R, toward),
                sl_trigger=sl_t, sl_limit=sl_l,
                target1=t1, target2=t2, qty=qty, capital_used=round(trig * qty, 2),
                max_loss=round(R * qty, 2), rr_net=round(rr_net, 2), probability=round(prob, 3))
```

### 9.4 Worked example (hypothetical numbers — not a live quote)

Stock priced around ₹100, previous-day high ₹100.90, ATR(14) = ₹2.00, capital ₹2,000, risk 2% (₹40), max position 50% (₹1,000):

**BUY signal**

| Order | Type | Trigger | Limit | Qty |
|---|---|---|---|---|
| 1. Entry | BUY, Stop-Limit (SL), validity DAY | ₹101.00 | ₹101.30 | 9 |
| 2. Protective stop (place right after fill) | SELL, Stop-Limit | ₹98.00 | ₹97.70 | 9 |
| 3. Target 1 | SELL, Limit | — | ₹107.00 | 4 |
| 4. Target 2 | SELL, Limit | — | ₹110.00 | 5 |

- **Chase protection:** if the live price in your broker app is already above ₹101.75 (trigger + 0.25R), skip the trade.
- Risk per share ₹3.00 → max loss ≈ **₹27** (≤ ₹40 cap). Capital used ≈ ₹909.
- Reward at T1 = 2R, at T2 = 3R, before costs.
- Use GTT/OCO if your broker supports it; otherwise manage stop + target manually.
- After T1 fills: move stop to entry (breakeven), then trail at `1.5 × ATR` below the highest close.
- Time stop: exit at close of day 5 if neither stop nor target hit.

**SELL signals come in two kinds:**

1. **Exit sell** (for an open long): target hit, stop hit, trailing stop hit, time stop, or model probability falls below 0.45 → sell at market/limit.
2. **Intraday short** (MIS only; cash-market delivery can't be shorted): mirror of the above — trigger = prev low − 2 ticks, SL = entry + 1.5×ATR, target below entry, hard exit by 15:10.

Prefer SL-**Limit** over SL-**Market** for stocks (some brokers restrict SL-M); the buffer (`SL_BUFFER`, default 0.3%) gives the stop room to fill.

### 9.5 JSON returned by the API

```json
{
  "id": 142,
  "symbol": "EXAMPLE.NS",
  "side": "BUY",
  "strategy": "swing",
  "product": "CNC",
  "entry": {"trigger": 101.00, "limit": 101.30, "skip_if_live_price_above": 101.75},
  "stop_loss": {"trigger": 98.00, "limit": 97.70},
  "targets": [107.00, 110.00],
  "qty": 9,
  "capital_used": 909.00,
  "max_loss": 27.00,
  "rr_net": 1.9,
  "probability": 0.63,
  "time_machine": {"p_target": 0.36, "p_stop": 0.34, "p_neither": 0.30, "expected_R_net": 0.30,
                   "replay": "19 fires: 8 target, 7 stop, 4 neither", "verdict": "EDGE"},
  "risk_gate": {"result": "PASS", "blocked_by": []},
  "model_version": "swing_lgbm_v17",
  "data_as_of": "2026-11-11T15:25:00+05:30",
  "data_source": "yfinance",
  "data_age_min": 16,
  "valid_until": "2026-11-12T15:30:00+05:30",
  "reasons": {"top_features": ["rsi14", "ema50_dist", "nifty_trend"], "gates": "all_passed"}
}
```

---

## 10. Time Machine — odds check before every trade

Before any ticket is shown, the app replays the trade two ways and tells you the honest odds. It is a **simulation, not a prediction**: it only knows what this stock did recently, not an earnings surprise, a halt or a headline.

**A. Monte Carlo (10,000 futures).** Each simulated day is drawn from the stock's own recent candles, expressed relative to the previous close.
- Swing: last ~250 daily candles, 5-day horizon.
- Intraday: 5-min candles from the last ~60 sessions (the `yfinance` limit), horizon = 5-min bars left until 15:10.

**B. Historical replay.** Run the *same setup rule* on every past session where it would have fired, with no look-ahead, and count what really happened: target first, stop first, neither.

```python
# timemachine/sim.py
import numpy as np, pandas as pd

def simulate(bars: pd.DataFrame, entry, stop, target, horizon=5, n=10_000, seed=19, lookback=250):
    """Bootstrap the stock's own candles (as % of previous close) into n possible futures. Long side;
    for shorts mirror the stop/target comparisons."""
    pc = bars["close"].shift(1)
    rel = np.column_stack([bars["open"] / pc - 1, bars["high"] / pc - 1,
                           bars["low"] / pc - 1, bars["close"] / pc - 1])[1:][-lookback:]
    rng = np.random.default_rng(seed)                           # fixed seed -> anyone can repeat it
    d = rel[rng.integers(0, len(rel), size=(n, horizon))]       # (n, horizon, 4)
    p = np.full(n, float(entry))
    done = np.zeros(n, dtype=bool)
    out = np.zeros(n, dtype=int)                                # +1 target first, -1 stop first, 0 neither
    for k in range(horizon):
        o, h, l, c = (p[:, None] * (1 + d[:, k, :])).T
        stop_hit = (l <= stop) & ~done
        tgt_hit = (h >= target) & ~done
        out[tgt_hit & ~stop_hit] = 1                            # both inside one candle -> stop first (conservative)
        out[stop_hit] = -1
        done |= stop_hit | tgt_hit
        p = np.where(done, p, c)
    pt, ps = (out == 1).mean(), (out == -1).mean()
    r_mult = (target - entry) / (entry - stop)
    return {"p_target": pt, "p_stop": ps, "p_neither": 1 - pt - ps, "expected_R": r_mult * pt - ps}


def replay(bars, setup_fn, levels_fn, horizon=5):
    """Same rule, every past session it fired. setup_fn/levels_fn only see data up to that session."""
    fires = tgt = stp = 0
    for i in range(60, len(bars) - horizon):
        hist = bars.iloc[: i + 1]
        if not setup_fn(hist):
            continue
        entry, stop, target = levels_fn(hist)
        fut = bars.iloc[i + 1 : i + 1 + horizon]
        if fut["high"].iloc[0] < entry:                         # trigger never crossed -> no trade
            continue
        fires += 1
        for _, b in fut.iterrows():
            if b["low"] <= stop:
                stp += 1; break                                 # stop first if both touched
            if b["high"] >= target:
                tgt += 1; break
    return {"fires": fires, "target": tgt, "stop": stp, "neither": fires - tgt - stp}


def verdict(sim, rep, cost_R, cfg):
    """cost_R = estimated costs / (risk per share * qty). The edge must survive costs."""
    edge = sim["expected_R"] - cost_R
    hit = rep["target"] / rep["fires"] if rep["fires"] else 0
    if edge >= cfg.TM_MIN_EDGE_R and rep["fires"] >= 15 and hit >= 0.35:
        return {"verdict": "EDGE", "expected_R_net": round(edge, 2)}
    return {"verdict": "NEGATIVE" if edge < -0.05 else "COIN_FLIP", "expected_R_net": round(edge, 2)}
```

| Verdict | Condition | Effect |
|---|---|---|
| `EDGE` | net expected R ≥ 0.15, ≥ 15 historical fires, target-first rate ≥ 35% | Signal allowed |
| `COIN_FLIP` | net expected R between −0.05 and 0.15, or too few historical fires | **Blocked** ("no proven edge") |
| `NEGATIVE` | net expected R < −0.05 | **Blocked** |

The result is saved on the signal (`Signal.time_machine`) and shown on the ticket, for example: *"target first 36%, stop first 34%, still open 30%; same setup fired 19 times: 8 target, 7 stop, 4 neither."*

---

## 11. Opening-range-breakout (ORB) scanner — optional intraday mode

**Off by default** (`INTRADAY_ENABLED = False`). Reasons: brokerage + STT eat a bigger share of a ₹1,000 intraday position, MIS positions are force-closed near the close, and a 15-minute-delayed feed cannot react to a breakout tick. Turn it on only after swing paper-trading passes the gates in §17.

**Delay-aware workflow (all times IST):**

| Time | What happens |
|---|---|
| 09:15–09:30 | Opening range forms (first three 5-min bars) |
| ~09:46 | The delayed feed now contains the full range → scan the shortlist |
| 09:46 | App emits **pending-order plans**: buy-stop above the range high, stop-loss at the range midpoint, target 2R |
| 09:46–14:00 | You place the pending orders in your own broker app; your broker's live feed triggers them. Breakouts before you place the order are missed, and that is accepted |
| 14:00 | Cancel any order that never triggered |
| 15:10 | Exit whatever is still open (MIS product, no leverage) |

**Setup filters:** range width 0.4–2.0% of price; opening-range volume ≥ 1.5× its 20-session average; price above the previous close (gap agrees with a long); Nifty above its EMA20 / VWAP; India VIX below threshold. Short-side (MIS only) is the mirror image.

```python
# scanner/orb.py
from signals.risk import round_tick, position_size, TICK

def opening_range(bars_5m_today):                              # IST-indexed 5-min bars
    r = bars_5m_today.between_time("09:15", "09:29")           # 09:15, 09:20, 09:25 bars
    return r["high"].max(), r["low"].min(), r["volume"].sum()

def orb_plan(symbol, bars_today, avg_or_volume, prev_close, nifty_ok, cfg):
    hi, lo, vol = opening_range(bars_today)
    width = (hi - lo) / hi
    if not (cfg.ORB_MIN_WIDTH <= width <= cfg.ORB_MAX_WIDTH):  # too tight = noise, too wide = poor reward:risk
        return None
    if vol < cfg.ORB_VOL_MULT * avg_or_volume:                 # needs real participation
        return None
    if not (hi > prev_close and nifty_ok):
        return None
    trig = round_tick(hi + TICK, "up")
    lim = round_tick(trig * (1 + cfg.ENTRY_BUFFER), "up")
    stop = round_tick((hi + lo) / 2, "down")                   # range midpoint
    R = trig - stop
    qty = position_size(trig, stop, cfg.CAPITAL, cfg.RISK_PER_TRADE, cfg.MAX_POSITION_PCT)
    if qty < 1:
        return None
    return dict(symbol=symbol, side="BUY", product="MIS", entry_trigger=trig, entry_limit=lim,
                sl_trigger=stop, sl_limit=round_tick(stop * (1 - cfg.SL_BUFFER), "down"),
                target1=round_tick(trig + cfg.T1_R * R, "down"), qty=qty,
                cancel_after="14:00", square_off_by="15:10")
```

Every ORB plan still goes through the Time Machine (§10, intraday version) and the risk gate (§12) before it is shown. Most plans will be blocked, and that is the point.

---

## 12. Risk gate — PASS / BLOCK on every order

The last check before a ticket appears. A blocked signal is stored (status `skipped`) with its reasons, shown on the **Blocked** page, and never displayed as an order.

| # | Check | Limit (from §13) |
|---|---|---|
| 1 | Kill switch | Must be off |
| 2 | Max loss on this trade | ≤ ₹40 |
| 3 | Daily / weekly loss caps | Not reached (₹100 / ₹200) |
| 4 | Trades today | < `MAX_TRADES_PER_DAY` (2) |
| 5 | Open positions | < 2 |
| 6 | Position size and cash | ≤ ₹1,000 and ≤ free cash |
| 7 | Net reward:risk after costs | ≥ 2.0 |
| 8 | Data age | ≤ `MAX_DATA_AGE_SEC` |
| 9 | Time Machine verdict | `EDGE` |
| 10 | Liquidity | qty ≤ 1% of 20-day average volume |
| 11 | Entry window | Swing: 08:45–14:30. Intraday: 09:46–14:00 |

```python
# signals/gate.py
from dataclasses import dataclass, field

@dataclass
class GateResult:
    passed: bool
    reasons: list = field(default_factory=list)

def risk_gate(o, pf, tm, data_age_min, cfg, now) -> GateResult:
    """o = order dict, pf = portfolio state, tm = Time Machine verdict dict."""
    why = []
    if pf.kill_switch:                                           why.append("kill switch is ON")
    if o["max_loss"] > cfg.CAPITAL * cfg.RISK_PER_TRADE:          why.append("max loss above per-trade limit")
    if pf.day_pnl <= -cfg.CAPITAL * cfg.DAILY_LOSS_CAP:           why.append("daily loss cap reached")
    if pf.week_pnl <= -cfg.CAPITAL * cfg.WEEKLY_LOSS_CAP:         why.append("weekly loss cap reached")
    if pf.trades_today >= cfg.MAX_TRADES_PER_DAY:                 why.append("too many trades today")
    if pf.open_positions >= cfg.MAX_OPEN_POSITIONS:               why.append("max open positions reached")
    if o["capital_used"] > min(cfg.CAPITAL * cfg.MAX_POSITION_PCT, pf.free_cash):
        why.append("position too big for limit or cash")
    if o["rr_net"] < cfg.MIN_RR:                                  why.append("net reward:risk below minimum")
    if data_age_min * 60 > cfg.MAX_DATA_AGE_SEC:                  why.append("data too old")
    if tm["verdict"] != "EDGE":                                   why.append("Time Machine: " + tm["verdict"])
    if o["qty"] > 0.01 * pf.avg_volume(o["symbol"]):              why.append("order too large for liquidity")
    if not in_entry_window(o, now):                               why.append("outside entry window")
    return GateResult(not why, why)
```

The ticket header reads **RISK GATE: PASS** or **BLOCK — <reasons>**.

---

## 13. Capital protection for ₹2,000

Configured in `config/trading_config.py`:

```python
CAPITAL = 2000
RISK_PER_TRADE = 0.02        # ₹40 max loss per trade
MAX_POSITION_PCT = 0.50      # ₹1,000 max per position
MAX_OPEN_POSITIONS = 2
DAILY_LOSS_CAP = 0.05        # ₹100 -> no new signals for the day
WEEKLY_LOSS_CAP = 0.10       # ₹200 -> pause until Monday
KILL_EQUITY = 1500           # equity <= ₹1,500 (-25%) -> signals OFF until you manually re-enable
MIN_PROB = 0.60
MIN_RR = 2.0
ATR_SL_MULT = 1.5
T1_R, T2_R = 2.0, 3.0
ENTRY_BUFFER, SL_BUFFER = 0.003, 0.003
PRODUCT = "CNC"              # swing default
NO_LEVERAGE = True           # never size with MIS leverage
MAX_TRADES_PER_DAY = 2
MAX_DATA_AGE_SEC = 1200      # delayed feed (15 min) + 5 min slack
TM_SIMS, TM_SEED, TM_MIN_EDGE_R = 10_000, 19, 0.15      # Time Machine (§10)
INTRADAY_ENABLED = False     # ORB scanner (§11) stays off until swing paper results justify it
ORB_MIN_WIDTH, ORB_MAX_WIDTH, ORB_VOL_MULT = 0.004, 0.02, 1.5
```

Rules the code enforces: never average down, never move a stop further away, no trading on stale data, no trade if costs eat the edge, kill switch is manual-reset only.

---

## 14. Scheduler timeline (IST, trading days only)

| Time | Job |
|---|---|
| 08:30 | Refresh instrument master, holiday calendar, ASM/GSM + results calendar |
| 08:45 | Pre-market plan: signals from last close; show gap risk |
| 09:15–15:30 | Poll the delayed feed every 5 min (shortlist + open signals only, to respect rate limits); log whether open signals' triggers/stops were crossed. Your broker enforces them live |
| 09:46 | Intraday mode only: ORB scan → pending-order plans (§11) |
| 14:00 | Intraday mode only: reminder to cancel untriggered ORB orders |
| 15:10 | Intraday square-off reminder (Telegram) |
| 16:00 | EOD backfill, corporate-action check, equity snapshot |
| 16:30 | Nightly incremental retrain + champion/challenger |
| Hourly | Data-freshness and drift checks |
| Sat 10:00 | Full retrain + Optuna |

```python
# marketdata/management/commands/run_worker.py
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
sched = BlockingScheduler(timezone="Asia/Kolkata")
sched.add_job(premarket_plan, CronTrigger(day_of_week="mon-fri", hour=8, minute=45))
sched.add_job(poll_delayed_feed, CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5"))
sched.add_job(orb_plans,      CronTrigger(day_of_week="mon-fri", hour=9, minute=46))   # only if INTRADAY_ENABLED
sched.add_job(eod_ingest,     CronTrigger(day_of_week="mon-fri", hour=16, minute=0))
sched.add_job(nightly_retrain, CronTrigger(day_of_week="mon-fri", hour=16, minute=30))
sched.add_job(weekly_retrain, CronTrigger(day_of_week="sat", hour=10, minute=0))
sched.add_job(drift_check,    CronTrigger(minute=5))
sched.start()
```

Every job wrapper checks the NSE holiday calendar and logs to `JobRun`.

---

## 15. REST API (DRF) and dashboard

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/signals/?status=new` | Current order tickets |
| GET | `/api/signals/{id}/` | One ticket (JSON in §9.5) |
| POST | `/api/signals/{id}/mark-taken/` | Record that you placed it (paper/live) |
| POST | `/api/signals/{id}/skip/` | Skip with reason |
| GET | `/api/positions/` | Open/closed positions, P&L |
| GET | `/api/quotes/{symbol}/` | Latest bar + data age |
| GET | `/api/performance/` | Equity curve, expectancy, drawdown |
| GET | `/api/models/` | Model versions, metrics, drift |
| POST | `/api/models/retrain/` | Trigger retrain (staff only) |
| POST | `/api/models/{id}/promote/` | Manual promote/rollback (staff only) |
| GET | `/api/signals/{id}/timemachine/` | Simulation odds + historical replay (§10) |
| GET | `/api/signals/{id}/gate/` | PASS/BLOCK result and reasons (§12) |
| GET | `/api/orb/` | Today's ORB pending-order plans (§11, intraday mode) |
| GET | `/api/health/` | Feed freshness, last job runs, kill-switch state |

**Pages (Django templates + HTMX polling every 10 s):**

- `/` Today's signals as cards: symbol, entry trigger/limit, SL trigger/limit, targets, qty, max loss, probability, Time Machine odds, **PASS/BLOCK** badge, data age badge, **Copy order** buttons
- `/blocked/` Signals the risk gate blocked, with reasons
- `/positions/` Open positions with live P&L and distance to stop/target
- `/performance/` Equity curve, win rate, expectancy (R), drawdown vs the ₹2,000 start
- `/models/` Champion vs candidates, metrics table, drift status, retrain button
- `/backtest/` Run a backtest for a date range and see trades
- `/settings/` View risk caps (edits require staff login)

```html
<!-- dashboard/templates/dashboard/signals.html -->
<div hx-get="{% url 'signals_partial' %}" hx-trigger="load, every 10s" hx-swap="innerHTML"></div>
```

---

## 16. Backtesting rules

- Signal on bar *t* close → entry only on bar *t+1* (trigger must actually be crossed), fills with slippage (0.05–0.10%)
- Costs from `estimate_costs`; intraday square-off enforced
- If stop and target are both inside one bar, assume **stop hit first** (conservative)
- Report on the exact rules used live: same gates, same sizing, ₹2,000 start equity
- Whole-share sizing matters at this capital — the backtest must use integer quantities

---

## 17. Go-live gates

| Gate | Requirement |
|---|---|
| Backtest | ≥ 3 years, net expectancy > 0.1R, max drawdown < 20% |
| Paper trading | ≥ 4 weeks **and** ≥ 30 signals, net expectancy > 0, drawdown < 15% |
| Data | Freshness failures < 1% of market minutes |
| Live start | Manual orders only, ₹2,000 cap, kill switch armed |

If a gate fails, keep paper trading — don't lower the bar.

---

## 18. Config (`.env.example`)

```ini
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=1
DATABASE_URL=sqlite:///db.sqlite3
DATA_PROVIDERS=yfinance,nse_eod   # priority order; no login or key needed
# Optional, only if you later add your own live feed (stays on your machine, never shared):
# LIVE_FEED_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TIMEZONE=Asia/Kolkata
```

Never commit `.env`. The app needs no broker credentials: you place orders in your own broker app.

---

## 19. Setup and run

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install django djangorestframework django-htmx pandas numpy pyarrow pandas-ta \
            lightgbm scikit-learn optuna joblib yfinance apscheduler requests \
            feedparser python-dotenv psycopg[binary] pytest pytest-django
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py backfill --years 5          # history for the universe
python manage.py train --mode swing          # first model -> becomes champion
python manage.py run_worker                  # terminal 1: scheduler + data polling
python manage.py runserver                   # terminal 2: dashboard at http://127.0.0.1:8000
```

---

## 20. Tests

- `round_tick`, `position_size`, `estimate_costs` (edge cases: tiny prices, qty = 0)
- **No look-ahead test:** features at time *t* are identical when future rows are deleted
- Triple-barrier labels on hand-made bars
- `assert_fresh` blocks stale data in market hours and allows pre-market plans
- Champion/challenger promotes only when all conditions pass; rollback works
- Kill switch and loss caps stop new signals
- API contract tests for `/api/signals/` and `/api/health/`
- Time Machine: same seed gives identical output; when stop and target sit in one candle the stop wins; `replay` never sees future bars
- Risk gate: each of the 11 checks blocks on its own, and PASS only when all clear
- ORB: opening range uses exactly the 09:15, 09:20 and 09:25 bars; no plan when range width or volume filters fail
- Staleness guard blocks a stalled feed and labels delayed data instead of calling it live

---

## 21. Build order (give these to Cursor one at a time)

1. Django skeleton + `Instrument`/`Bar` models + yfinance provider + `backfill` command
2. NSE end-of-day provider + failover service + staleness guard (no broker anywhere)
3. Feature builder + triple-barrier labels + no-look-ahead tests
4. Walk-forward trainer + LightGBM + calibration + `ModelVersion` registry
5. Backtest engine with costs and slippage
6. `risk.py` + `engine.py` + `Signal` model + gates (with chase limit)
7. Time Machine: `simulate()` + `replay()` + verdict (§10)
8. Risk gate: `gate.py` + Blocked page (§12)
9. DRF API + dashboard templates (HTMX)
10. APScheduler worker + retrain/drift jobs + champion/challenger
11. Telegram alerts + portfolio caps + kill switch
12. Paper-trading mode and performance page
13. Optional: ORB scanner for intraday mode (§11)

---

## 22. Roadmap (after v1)

- Optional real-time `LiveFeedProvider` using your own keys (kept on your machine) if 15-minute-delayed data ever limits you
- Optional broker order placement behind a feature flag (check SEBI/broker retail-algo rules first)
- News/sentiment features, sector rotation model, Nifty options-based regime signals
- SHAP explanations on each signal card
- PostgreSQL + Celery/Redis if load grows
- Multi-timeframe confirmation (daily trend + 15-min entry)

---

*Educational/decision-support software. Not investment advice. Trading in equities involves risk of loss, including loss of the entire capital.*
