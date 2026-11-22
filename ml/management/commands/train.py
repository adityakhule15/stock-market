from django.core.management.base import BaseCommand

from ml.retrain import retrain_and_maybe_promote


class Command(BaseCommand):
    help = "Train swing model and promote if it beats champion"

    def add_arguments(self, parser):
        parser.add_argument("--mode", type=str, default="swing")
        parser.add_argument("--full", action="store_true")
        parser.add_argument("--trials", type=int, default=20)
        parser.add_argument(
            "--retire-legacy",
            action="store_true",
            help="Retire in-sample champions before promoting an honest model",
        )

    def handle(self, *args, **options):
        if options["mode"] != "swing":
            self.stdout.write(self.style.WARNING("Only swing mode implemented in v1; proceeding as swing."))
        mv, promoted = retrain_and_maybe_promote(
            full=options["full"],
            n_trials=options["trials"],
            retire_legacy=options["retire_legacy"] or options["full"],
        )
        self.stdout.write(
            self.style.SUCCESS(f"Saved {mv.name}_v{mv.version} status={mv.status} promoted={promoted}")
        )
        self.stdout.write(str(mv.metrics))
