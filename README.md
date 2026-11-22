# StockScope India

Local Django app for **NSE equities + F&O ideas** on a **₹2,000 wallet**.

It scans market data, scores Normal (CNC) and F&O debit tickets with **LightGBM + rules**, shows **top wallet-fit suggestions net of Groww charges**, tracks open/closed P&L, and learns from mistakes overnight.

**It never places broker orders.** You place every trade yourself in Groww (or any broker). Optional INDstocks JWT is used for **live LTP / option chain only**.

Deeper HLD/LLD: [STOCKSCOPE_FULL_ARCHITECTURE.md](STOCKSCOPE_FULL_ARCHITECTURE.md)

---

## What this is (in one page)

| Layer | Role |
|-------|------|
| **Today (Signals)** | Ranked Normal + F&O ideas that fit the wallet after charges |
| **Advisor** | Guided NIFTY/BANKNIFTY CALL/PUT coach + free chat |
| **Positions** | Groww-style book: live LTP, Open/Today P&L, HOLD/SELL advice |
| **Journal** | Date day-book (gross / charges / net) + weekly review + retrain trigger |
| **Settings** | F&O watchlist, kill switch, INDstocks token, live refresh seconds |
| **ML** | Champion LightGBM + meta filter; nightly/weekly retrain with promote guards |
| **Capital defence** | ALIVE → CAUTION → SURVIVAL → DEAD; lockouts; F&O pause; “sit out” messaging |

**Product goal after drawdown:** stop stacking losses — not magically recover capital. Empty board beats another weak ticket.

---

## Non‑negotiable rules

1. **Signals only** — `ALLOW_BROKER_ORDERS = False`. No `/order`, no Groww/INDstocks place APIs.
2. **₹2,000 start** — `CAPITAL = 2000` in `config/trading_config.py`.
3. **You place orders** — confirm fill price in the UI so the wallet/P&L stay honest.
4. **Net after Groww charges** — ideas that lose to fees are filtered out (`net_ok`).
5. **INDstocks = data only** — LTP / quotes / option chain; token never used to trade.
6. **Dummy positions** — practice rows do **not** affect wallet or day totals.

---

## Quick start

```bash
python -m pip install -r requirements.txt
copy .env.example .env
python manage.py bootstrap --force-train
python manage.py run --port 8001
```

Open **http://127.0.0.1:8001**

| Command | What it does |
|---------|----------------|
| `python manage.py run --port 8001` | Web + **embedded** APScheduler (default) |
| `python manage.py run_worker` | Scheduler only |
| `python manage.py bootstrap --force-train` | migrate, universe, backfill, train + promote |
| `DISABLE_SCHEDULER=1` | Web without cron jobs |

### Desktop shortcut (Windows / Chrome)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1
```

Double-click **StockScope India** → server on port **8001** + Chrome.

---

## Environment (`.env`)

Copy from `.env.example`. Important keys:

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` | Django |
| `DATABASE_URL` | SQLite default / Postgres in prod |
| `DATA_PROVIDERS` | e.g. `yfinance,nse_eod` |
| `INDSTOCKS_ACCESS_TOKEN` | Optional live LTP/chain (also savable in Settings DB until JWT exp) |
| `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` | Optional Advisor AI (local coach works without keys) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional alerts |
| `PORT` | Default `8001` |
| `RUN_EMBEDDED_SCHEDULER` | One-process jobs (default on) |
| `TIMEZONE` | `Asia/Kolkata` |

INDstocks access tokens: https://www.indstocks.com/app/api-trading/access-tokens (expire ~24h — regenerate daily or paste in **Settings**).

---

## Pages (what you use day to day)

| Path | Feature |
|------|---------|
| `/` | **Today** — scan / rank Normal + F&O; confirm or skip |
| `/advisor/` | Guided F&O coach + free chat |
| `/positions/` | Open/closed book, live marks, day strip, edit / square off / dummy |
| `/journal/` | Date picker day-book (gross, charges, net) + retrain |
| `/journal/weekly/` | Weekly review |
| `/insights/` | Insights (+ links to Models / Backtest / Blocked) |
| `/performance/` | Equity, day/week P&L, paper gates |
| `/models/` | Champion / candidate LightGBM |
| `/backtest/` | Historical engine |
| `/settings/` | Watchlist, kill switch, token, refresh sec, life reset |
| `/blocked/` | Gate failures |

---

## What we built (feature catalogue)

### 1. Signals / Today

- Scans Normal CNC universe + F&O underlyings from **Settings watchlist**.
- Ranks top ideas by wallet-aware score (`dashboard/ranking.py`).
- Life-aware caps: **ALIVE top 3**, **SURVIVAL top 1**, **DEAD none**.
- Each card: entry / SL / targets, qty or **1 lot × lot_size**, probability, Time Machine, PASS/BLOCK gate, Groww net tip, **affordable lots** hint.
- Confirm: **Did you place this?** → fill price → `Position` + `OrderFill` BUY; or Skip.
- Session stage (IST): F&O entry windows, square-off reminders, “Buy for tomorrow”.
- No-trade copy when board is empty: **sit out rather than stack another loss**.

### 2. Advisor

- Guided: NIFTY / BANKNIFTY → CALL/PUT → auto or live strike → **BUY or SKIP** sized to wallet.
- Free chat: local keyword coach always; optional Gemini / DeepSeek.
- APIs: `POST /api/advisor/start/`, `POST /api/advisor/chat/`.

### 3. Positions (Groww-style book)

- Filters: Open / Closed / All; **Original vs Dummy**.
- Strip: Invested, Current, **Open P&L**, **Today P&L** (closed realized today + open MTM).
- Live LTP poll interval from Settings (`live_refresh_sec`, default 10s); pauses when tab hidden.
- Hub: `portfolio/live_hub.py`.
- Actions: Reanalyze · Edit (lots, entry, SL, target) · Delete · Square off.
- **+ Add Groww order** for manual / dummy rows.
- Position advice: HOLD / MOVE SL / TRAIL / **EXIT_NOW** on deep adverse R or F&O premium drop (`ADVERSE_R_EXIT`, `FNO_PREMIUM_DROP_EXIT`).

### 4. Day book & Journal

- `portfolio/day_book.py`: date-scoped fills/positions with **gross, charges, net**.
- Day totals = **closed realized that day + open MTM** for today’s activity (not closed-only).
- Dummy rows excluded from wallet and day totals.
- Journal can trigger meta retrain / review mistakes.

### 5. Capital protection (no repeated losses)

| Band | Equity (approx) | Behaviour |
|------|-----------------|-----------|
| **ALIVE** | > ₹1,850 | Default risk; top 3 ideas |
| **CAUTION** | ≤ ₹1,850 | Higher `min_prob`, smaller risk, clear **CAPITAL PROTECT** banner |
| **F&O pause** | < ₹1,800 | No new F&O suggestions until recovery |
| **SURVIVAL** | ≤ ₹1,700 | Top 1 only, stricter gates — stop the bleed |
| **DEAD** | ≤ ₹800 | No suggestions until staff/manual reset |
| **Kill** | ~₹1,500 mid | Kill switch can arm |
| **Lockout** | 2 consecutive losses | Cooling-off ~24h |

Banner + no-trade messaging stress: **do not chase / stack losses**; sitting out is the trade. Retrain after ~14% drawdown improves the model and raises bars — it does **not** invent a rebound.

### 6. INDstocks token

- Prefer **Settings DB** token (`indstocks_access_token`) until JWT `exp`; then clear.
- Fallback: `INDSTOCKS_ACCESS_TOKEN` in `.env`.
- Modal / Settings help points at the access-tokens URL.
- Data-only: quotes and option chain.

### 7. Groww charges

- `portfolio/charges.py`: brokerage, STT, exchange, SEBI, stamp, GST, DP where needed.
- Ranking drops tickets with `net_ok=False`.

### 8. ML & retrain

- Features → LightGBM (`ml/train.py`, Optuna on weekly full retrain).
- Registry: champion / candidate under `artifacts/` (`ml/registry.py`).
- Promote only if expectancy / hit-rate improve (guards in `ml/retrain.py`).
- **Meta filter** (`ml/meta_filter.py`): learn from mistake tags; soft-block weak P(win).
- Nightly incremental + weekly Optuna jobs (IST scheduler).
- Drift PSI checks → optional retrain.

### 9. Pipeline (day-trader stages)

Stored on each `Signal.pipeline` (9 stages): scanner → news → entry → exit → time_machine → budget → gate → ticket → alerts (`signals/pipeline.py` and helpers).

### 10. Market data

- Providers: yfinance (~15m delay), NSE EOD, optional INDstocks live.
- Regime: NIFTY + India VIX (`marketdata/regime.py`).
- Event flags / results warn (`marketdata/events.py`).
- Universe filters (price, ADV) in `trading_config.py`.

---

## How it works

### Normal CNC idea

```
Universe symbols
  → bars (yfinance / cache / EOD)
  → features + champion LightGBM P(up)
  → build_order (entry / SL / T1 / T2, qty, RR)
  → Time Machine edge check
  → risk_gate (cash, life, windows, kill, lockout, meta)
  → Signal row
  → ranking + trade_economics (net after charges)
  → Today board (if PASS + net_ok + fits wallet)
```

### F&O debit idea

```
Settings fno_watchlist (e.g. NIFTY, BANKNIFTY)
  → build_fno_plan (spot, ATR, expiry, chain, strike ≤ wallet)
  → 1-lot default + affordable_lots hint
  → net_ok via Groww economics
  → F&O section on Today (paused if equity < FNO_PAUSE_BELOW_EQUITY)
```

### Confirm → manage → close

```
Confirm fill
  → Position OPEN + reserved_cash + OrderFill BUY

Live marks (while Positions visible)
  → LTP / MTM · suggest HOLD / EXIT_NOW / trail

Square off
  → OrderFill SELL + charges
  → pnl net · cash ↑ · life_status refresh · consecutive_losses / lockout
```

### Day P&L strip

```
Today P&L = realized on positions closed today (IST)
          + mark-to-market on open positions that count for today
Open P&L  = MTM on currently open (non-dummy) positions
```

---

## Capital & risk knobs (`config/trading_config.py`)

| Constant | Typical | Role |
|----------|---------|------|
| `CAPITAL` | 2000 | Start wallet |
| `DEAD_FLOOR` / `SURVIVAL_EQUITY` | 800 / 1700 | Life bands |
| `CAUTION_EQUITY` | 1850 | Soft tighten after drawdown |
| `FNO_PAUSE_BELOW_EQUITY` | 1800 | Block new F&O |
| `RISK_PER_TRADE` | 1% | Max loss sizing |
| `MAX_OPEN_POSITIONS` | 1 | Concurrency |
| `MAX_TRADES_PER_DAY` | 1 | Daily pace |
| `MIN_PROB` / `CAUTION_MIN_PROB` | 0.55 / 0.58 | LightGBM bar |
| `FNO_MIN_PROB` | 0.58 | F&O bar |
| `MIN_RR` | 1.6 net | Edge after fees |
| `COOLING_OFF_LOSSES` / `HOURS` | 2 / 24 | Lockout |
| `ADVERSE_R_EXIT` | −0.50R | Force EXIT_NOW |
| `FNO_PREMIUM_DROP_EXIT` | 20% | Option decay exit |
| `META_MIN_WIN_PROB` | 0.42 | Meta soft gate |
| `ALLOW_BROKER_ORDERS` | **False** | Hard lock |
| `SQUARE_OFF_BY` | 15:10 IST | F&O reminder |

---

## Repository map

```
stock-market/
├── config/                 # Django settings, trading_config (capital / fees / risk)
├── marketdata/             # Instruments, bars, providers, option chain, jobs, scheduler
├── features/               # Indicators, builders, labels
├── ml/                     # LightGBM train / evaluate / registry / retrain / meta_filter
├── signals/                # Tickets, F&O, gate, pipeline, scan tasks
├── portfolio/              # Wallet, positions, fills, charges, day_book, live hub
├── dashboard/              # Templates, views, DRF API, ranking, advisor
├── scanner/                # ORB + patterns
├── timemachine/            # Monte Carlo / replay before trade
├── backtest/               # Historical engine
├── notifications/          # Optional Telegram
├── scripts/                # Desktop shortcut / Chrome
├── tests/                  # pytest (incl. no-broker-order guarantee)
├── artifacts/              # Model joblib bundles
├── manage.py
├── requirements.txt
├── render.yaml / railway.toml
├── README.md               ← this file
└── STOCKSCOPE_FULL_ARCHITECTURE.md
```

---

## Typical daily loop

1. **Pre-market** — ensure INDstocks token fresh (Settings or `.env`); check life banner.
2. **Scan Today** — take only PASS + net_ok ideas that fit wallet; prefer sit-out in CAUTION/SURVIVAL.
3. **Confirm** — paste real Groww fill price so P&L is real.
4. **Positions** — watch Open + Today P&L; follow EXIT_NOW / trail advice; square off by ~15:10 for F&O.
5. **Journal** — pick the date → review gross / charges / net; tag mistakes.
6. **Overnight** — scheduler retrains; promote only if metrics improve; meta filter learns “don’t repeat that”.

---

## Order confirm → P&L (short)

1. On a signal: **Did you place this order?**
2. Enter broker **fill price** → Yes (or No → skip).
3. On **Positions**, manage with live marks → exit price → P&L + equity / life verdict.

---

## Scheduler (IST, embedded)

| Job | When (approx) |
|-----|----------------|
| refresh_master | Mon–Fri 08:30 |
| premarket_plan | 08:45 |
| poll_delayed_feed | 09–15 every 5m |
| orb_plans | 09:46 |
| square_off_reminder | 15:10 |
| eod_ingest | 16:00 |
| nightly_retrain | 16:30 |
| weekly_retrain | Sat 10:00 |
| drift_check | :05 hourly |

---

## Deploy & CI

- Render Blueprint: [`render.yaml`](render.yaml)
- Railway: [`railway.toml`](railway.toml)
- CI: [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
- Tests: `pytest` (includes **no broker orders** guarantee)

---

## Advisor chat notes

- **Guided** — NIFTY/BANKNIFTY → CALL/PUT → auto or live strike → BUY/SKIP sized to wallet.
- **Free chat** — local coach without keys; optional `GEMINI_API_KEY` or `DEEPSEEK_API_KEY`.

Wallet amount shows on every page. StockScope never places broker orders.

---

## Related docs

| Doc | Contents |
|-----|----------|
| [STOCKSCOPE_FULL_ARCHITECTURE.md](STOCKSCOPE_FULL_ARCHITECTURE.md) | Full HLD/LLD, API tables, sequences, model fields |
| `.env.example` | Env template |
| `config/trading_config.py` | All capital / risk / fee constants |

---

## Disclaimer

This is a **research / paper-trading helper**, not financial advice. Markets can lose money. Survival floors and “sit out” messaging exist to **protect a small wallet**, not to guarantee profits or recover losses.
