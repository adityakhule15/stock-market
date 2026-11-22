# Research agents — measured results

**Status:** scaffold only. No live LLM backtest has been run yet.  
**Do not claim improved accuracy** until tables below are filled from out-of-sample runs, **net of Groww charges**, versus NIFTY buy-and-hold.

## Setup for a fair comparison

1. Keep `AGENTS_ENABLED=0` until you accept LLM cost.
2. Install sidecar: `python -m venv .venv-ta` then  
   `.venv-ta\Scripts\pip install -r requirements-tradingagents.txt`
3. Run LightGBM-only baseline via `/backtest/` or existing CNC engine.
4. Run agent grid (spend approval required):

```bash
python manage.py ta_backtest --tickers RELIANCE.NS,TCS.NS --start YYYY-MM-DD --end YYYY-MM-DD --every 7
```

5. Replay StockScope signals with/without agent veto on the same window.
6. Fill the tables. State plainly whether integration helped.

## Comparison tables (fill after approved spend)

### (a) Original StockScope signals (LightGBM + risk_gate + charges)

Walk-forward via `backtest.engine.run_backtest` (shared ticket simulator, Groww charges, 1 position at a time). Run after honest train:

```bash
python manage.py train --mode swing --full --retire-legacy
python -c "from backtest.engine import run_backtest; from marketdata.service import MarketDataService; b,_=MarketDataService().history('RELIANCE.NS',period='5y'); r=run_backtest('RELIANCE.NS',b); print(r.metrics)"
```

| Window | N signals | Hit rate | Expectancy (₹ net) | Max DD | Notes |
|--------|-----------|----------|--------------------|--------|-------|
| OOS holdout (post-honest train) | _fill_ | _fill_ | _fill_ | _fill_ | Net of Groww CNC charges; compare `nifty_return` in metrics |

### (b) Agents-only ratings (TradingAgents grid)

| Window | Cells | Buy/OW | Hold | UW/Sell | Alpha vs ^NSEI | Notes |
|--------|-------|--------|------|---------|----------------|-------|
| TBD | | | | | | |

### (c) StockScope + agent veto

| Window | N kept | N vetoed | Hit rate | Expectancy (₹ net) | vs (a) | Notes |
|--------|--------|----------|----------|--------------------|--------|-------|
| TBD | | | | | | |

### NIFTY buy-and-hold (^NSEI)

| Window | Return | Max DD | Notes |
|--------|--------|--------|-------|
| TBD | | | |

## Plain verdict

_Not measured yet for TradingAgents._ After filling the tables above, replace this sentence with a one-line statement of whether (c) beat (a) and NIFTY B&H on the chosen OOS window, net of charges — or that it did not.

---

## Kronos local forecast (measured, not a trading claim)

Walk-forward on **RELIANCE.NS** only, from **2025-09-01** (earlier dates may sit inside Kronos pretraining), sampled every 10 sessions, horizon 5 daily bars, 8 CPU paths, model `NeoQuasar/Kronos-small`.

| Window | n | Direction hit rate | Target-first rate | Stop-first rate | Notes |
|--------|---|--------------------|-------------------|-----------------|-------|
| 2025-09-01 → run date | 30 | 0.533 | 0.300 | 0.467 | ATR stop 1.0× / target 1.5×; **not** net of Groww charges; not a PASS/BLOCK claim |

Command:

```bash
python manage.py kronos_backtest --symbol RELIANCE --every 10 --paths 8 --horizon 5
```

**Plain note:** 53.3% direction hit rate on this small sampled window is near a coin flip. Do not treat Kronos alone as an edge. Consensus remains filter-mode on top of LightGBM + risk_gate.
