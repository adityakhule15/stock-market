# StockScope India

AI stock market analyzer for NSE equities — **signals only**, ₹2,000 capital.
**Never places broker orders** (Groww / INDstocks / any API). You place trades yourself.
Optional INDstocks token = live LTP/option-chain only (`INDSTOCKS_ACCESS_TOKEN` in `.env`).
Never calls `/order` or `/smart/order`. Local LightGBM (free).

See [stockscope_india_project.md](stockscope_india_project.md) for the full design.

## Desktop icon (Chrome · port 8001)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1
```

Double-click **StockScope India** on your Desktop → background server + jobs on **http://127.0.0.1:8001** and opens Chrome.

## One command

```bash
python -m pip install -r requirements.txt
copy .env.example .env
python manage.py bootstrap --force-train
python manage.py run --port 8001
```

Default port is **8001**. Scheduler is embedded — no second terminal.

## Advisor chat

Open **Advisor** (`/advisor/`):
- **Guided** — NIFTY/BANKNIFTY → CALL/PUT → auto or live strike → **BUY or SKIP** sized to your wallet.
- **Free chat** — ask anything (local coach works with no key). Optional free AI: set `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey) or `DEEPSEEK_API_KEY` in `.env`.

Wallet amount is shown on every page. StockScope never places broker orders.

## Order confirm → P&L

1. On a signal: **Did you place this order?**
2. Enter broker **fill price** → Yes (or No → skip).
3. On **Positions**, enter **exit price** → P&L + equity verdict.

## Deploy

Render Blueprint: [`render.yaml`](render.yaml). CI: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Survival

ALIVE → SURVIVAL (≤ ₹1,700) → DEAD (≤ ₹800). Paper until go-live gates pass.


https://github.com/Jakeschincariol/day-trader-skill

