from django.core.management.base import BaseCommand

from indexdesk.constants import DAILY_YEARS
from indexdesk.data import backfill_index_history


class Command(BaseCommand):
    help = "Backfill 5y daily + rolling 5m/1m for NIFTY and BANKNIFTY"

    def add_arguments(self, parser):
        parser.add_argument("--years", type=int, default=DAILY_YEARS)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        detail = backfill_index_history(years=int(options["years"]), force=bool(options["force"]))
        self.stdout.write(self.style.SUCCESS(str(detail)))
