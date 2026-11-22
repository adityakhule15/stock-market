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

| Window | N signals | Hit rate | Expectancy (₹ net) | Max DD | Notes |
|--------|-----------|----------|--------------------|--------|-------|
| TBD | | | | | |

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

_Not measured yet._ After filling the tables above, replace this sentence with a one-line statement of whether (c) beat (a) and NIFTY B&H on the chosen OOS window, net of charges — or that it did not.
