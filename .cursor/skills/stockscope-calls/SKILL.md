---
name: stockscope-calls
description: Present StockScope India's combined multi-model calls for today. Use when the user asks for "the calls", "give me the calls", "today's calls", or combined model votes.
---

# StockScope combined calls

When the user asks for calls:

1. Run `python manage.py calls_today --json` from the repo root (use `.venv\Scripts\python` on Windows).
2. Optionally refresh first with `python manage.py calls_today --refresh --json` if they want a fresh review (may take minutes; Kronos is local CPU, TradingAgents costs LLM money).
3. Present a compact table: symbol, LightGBM prob, Time Machine, Kronos, TradingAgents, insider, combined call + score + reason.
4. Include the day-trader checklist status when relevant.
5. Hard rules to state clearly:
   - Risk gate **BLOCK** is final (consensus cannot unblock).
   - **No broker orders** — tickets are Groww copy only (`ALLOW_BROKER_ORDERS=False`).
   - Analyst is facts-only and never votes.
   - Do not claim improved accuracy; report measured numbers only when `docs/RESULTS.md` has them.

Never invent calls. If the command returns zero rows, tell the user to Scan on Today first.
