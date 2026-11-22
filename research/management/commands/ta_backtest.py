"""Expose upstream TradingAgents backtest via Django manage.py."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from research.adapter import build_portfolio_context, build_ta_config, invoke_runner, load_agents_settings


class Command(BaseCommand):
    help = (
        "Run TradingAgents decision-quality backtest "
        "(sidecar). Example: manage.py ta_backtest --tickers RELIANCE.NS "
        "--start 2024-01-01 --end 2024-06-01 --every 7"
    )

    def add_arguments(self, parser):
        parser.add_argument("--tickers", required=True, help="Comma-separated Yahoo tickers")
        parser.add_argument("--start", required=True, help="YYYY-MM-DD")
        parser.add_argument("--end", required=True, help="YYYY-MM-DD")
        parser.add_argument("--every", type=int, default=7)
        parser.add_argument("--analysts", default="", help="market,social,news,fundamentals")
        parser.add_argument("--run-id", default="", help="Resume prior run_id")
        parser.add_argument(
            "--no-portfolio",
            action="store_true",
            help="Omit portfolio context (unknown book)",
        )

    def handle(self, *args, **options):
        agents = load_agents_settings()
        tickers = [t.strip() for t in options["tickers"].split(",") if t.strip()]
        if not tickers:
            raise CommandError("No tickers")
        analysts = options["analysts"]
        book = None if options["no_portfolio"] else build_portfolio_context(known=True)
        req = {
            "action": "backtest",
            "tickers": tickers,
            "start": options["start"],
            "end": options["end"],
            "every": int(options["every"]),
            "selected_analysts": [a.strip() for a in analysts.split(",") if a.strip()]
            or agents.get("selected_analysts"),
            "run_id": options["run_id"] or None,
            "portfolio": book,
            "config": build_ta_config(),
        }
        self.stdout.write("Invoking TradingAgents sidecar backtest…")
        result = invoke_runner(
            req,
            timeout_sec=max(3600, int(agents.get("timeout_sec") or 600) * max(1, len(tickers))),
        )
        if not result.get("ok"):
            raise CommandError(result.get("error") or "backtest failed")
        self.stdout.write(self.style.SUCCESS(f"run_id={result.get('run_id')}"))
        if result.get("summary_text"):
            self.stdout.write(result["summary_text"])
        self.stdout.write(f"log: {result.get('log_path')}")
        for f in result.get("failures") or []:
            self.stdout.write(self.style.WARNING(f"failed: {f}"))
