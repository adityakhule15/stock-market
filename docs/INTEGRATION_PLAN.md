# TradingAgents → StockScope India — Integration Plan

**Status:** approved for implementation  
**Upstream:** [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) `v0.7.1` (Apache-2.0, Python ≥ 3.11)  
**Goal:** reuse the framework **as-is** (agents, graph, memory, backtester). Write only Django glue.

---

## 0. Environment check

| Item | StockScope today | TradingAgents `v0.7.1` | Verdict |
|------|------------------|------------------------|---------|
| Python | **3.12.10** (`.venv`) | ≥ 3.11 | OK |
| Django | 6.x (`django>=5,<7`) | not a dependency | OK |
| pandas | pinned `>=2.2,<3` (Windows WDAC) | **`pandas>=3.0.6`** | **HARD CONFLICT** |
| LightGBM | `<4.6` | none | OK if pandas stays 2.x in app venv |
| langchain / langgraph | not installed | required by TA | Sidecar only |

**Conclusion:** same-venv install is unsafe. Use **Option A isolated** (`.venv-ta` + optional Docker).

---

## 1. Upstream modules (reuse unchanged)

| Module | Role | Reuse? |
|--------|------|--------|
| `tradingagents/graph/` | LangGraph orchestration | Yes, as-is |
| `tradingagents/agents/` | Analysts, debates, trader, PM, 5-tier ratings | Yes, as-is |
| `tradingagents/dataflows/` | Yahoo / SEC / FRED / Polymarket vendors | Yes, as-is |
| `tradingagents/portfolio.py` | `PortfolioContext` | Yes — build from wallet |
| `tradingagents/memory/` | Memory log + settlement | Yes — path + nightly job |
| `tradingagents/backtest.py` | Decision-quality grid | Yes — `manage.py ta_backtest` |
| `tradingagents/reporting.py`, `report_html.py` | Reports + HTML | Yes |
| `tradingagents/default_config.py` | Defaults; `.NS` → `^NSEI` | Yes |
| `tradingagents/llm_clients/` | All providers | Yes |
| `cli/` | Interactive CLI | Document + Docker |

---

## 2. Install (locked)

1. Pin GitHub release wheel in `requirements-tradingagents.txt` (`tradingagents==0.7.1`).
2. Install into **`.venv-ta/`** only — Django `.venv` unchanged.
3. Django talks via **subprocess JSON runner** (`scripts/ta_runner.py`).
4. Docker Compose: `web` + `research-agents` with shared volumes.
5. Root `NOTICE` + README third-party notices (Tauric Research, Apache-2.0).
6. Production: `TRADINGAGENTS_NO_UPDATE_CHECK=1`.

Optional paid vendors (Alpha Vantage, TypeSafe Jev) and FRED stay **off** until explicitly approved.

---

## 3. Glue — `research/` app

- `adapter.py` — config, ticker map, portfolio build, sidecar invoke
- `models.py` — `ResearchRun`
- `services.py` — `run_analysis` / enqueue / fail-open
- `veto.py` — rating → keep / neutral / `agents_veto` (never unblock)
- `workers.py` — background / APScheduler
- Commands: `ta_backtest`, `ta_settle_memory`
- Tests: mocked LLM / mocked runner — no live calls in CI

### Config keys (default agents OFF)

`AGENTS_ENABLED=False`, `AGENTS_TOP_N=3`, daily call/cost caps, timeout, retry, debate/risk rounds, selected analysts, checkpoint on.

### Pipeline

```
scan → features → LightGBM → risk_gate → [agents top-N PASS] → rank → UI
```

| Rating | Action |
|--------|--------|
| Buy / Overweight | Keep PASS; mild rank boost |
| Hold | Keep PASS; `agents_neutral`; lower rank |
| Underweight / Sell | BLOCK + `agents_veto` |
| REVIEW / error | Fail-open + `agents unavailable` |

Agents cannot turn BLOCK→PASS or override capital rules. F&O: underlying only.

---

## 4. Privacy & cost

**Sent to LLM provider only:** ticker, date, public market/news data, optional INR cash/qty/avg, memory lessons.  
**Never:** INDstocks token, Groww creds, Django secrets.

**Cost:** full multi-agent run = many LLM calls per symbol. Keep `AGENTS_ENABLED` off until cost is accepted. Prefer DeepSeek / Gemini Flash / Ollama for experiments.

---

## 5. Deliverables

- This plan, README “Research agents”, `NOTICE`
- `requirements-tradingagents.txt`, Dockerfile, docker-compose, `.env.example`
- `research/` app, migrations, mocked tests
- Pipeline veto + UI
- `docs/RESULTS.md` (measured comparison after approved spend)
