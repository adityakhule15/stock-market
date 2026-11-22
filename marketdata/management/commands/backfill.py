from django.core.management.base import BaseCommand

from marketdata.service import MarketDataService
from marketdata.universe import DEFAULT_UNIVERSE, ensure_seed_instruments


class Command(BaseCommand):
    help = "Backfill OHLCV history for the tradable universe via public providers"

    def add_arguments(self, parser):
        parser.add_argument("--years", type=int, default=5)
        parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
        parser.add_argument("--interval", type=str, default="1d")

    def handle(self, *args, **options):
        ensure_seed_instruments()
        if options["symbols"]:
            symbols = [s.strip() for s in options["symbols"].split(",") if s.strip()]
        else:
            symbols = DEFAULT_UNIVERSE
        svc = MarketDataService()
        self.stdout.write(f"Backfilling {len(symbols)} symbols ({options['years']}y)...")
        results = {}
        for sym in symbols:
            try:
                n = svc.backfill_symbol(sym, years=options["years"], interval=options["interval"])
                results[sym] = n
                self.stdout.write(self.style.SUCCESS(f"  {sym}: {n} bars"))
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"  {sym}: {exc}"))
                results[sym] = 0
        ok = sum(1 for v in results.values() if v > 0)
        self.stdout.write(self.style.SUCCESS(f"Done: {ok}/{len(symbols)} symbols"))
