from django.core.management.base import BaseCommand

from indexdesk.train import train_index_model


class Command(BaseCommand):
    help = "Train index_pattern_lgbm on NIFTY + BANKNIFTY and maybe promote"

    def add_arguments(self, parser):
        parser.add_argument("--no-promote", action="store_true")

    def handle(self, *args, **options):
        mv, promoted, metrics = train_index_model(promote=not options["no_promote"])
        self.stdout.write(
            self.style.SUCCESS(
                f"v{mv.version} promoted={promoted} "
                f"exp={metrics.get('expectancy_R')} auc={metrics.get('auc')} n={metrics.get('n_rows')}"
            )
        )
