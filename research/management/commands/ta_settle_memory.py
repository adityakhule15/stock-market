"""Settle pending TradingAgents memory-log decisions (nightly / manual)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from research.services import settle_memory


class Command(BaseCommand):
    help = "Call TradingAgents settle_all_pending() via sidecar (after eod_ingest)."

    def add_arguments(self, parser):
        parser.add_argument("--no-wait", action="store_true")

    def handle(self, *args, **options):
        result = settle_memory(wait=not options["no_wait"])
        if not result.get("ok"):
            raise CommandError(result.get("error") or "settle failed")
        settled = result.get("settled") or []
        failed = result.get("failed") or []
        self.stdout.write(self.style.SUCCESS(f"settled={len(settled)} failed={len(failed)}"))
        for row in failed:
            self.stdout.write(self.style.WARNING(str(row)))
