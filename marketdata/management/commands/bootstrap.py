"""Prepare data + model so desktop launch is ready.

Backfill runs ONLY when history is missing, too short, or stale.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from marketdata.backfill_needed import backfill_status_summary
from marketdata.universe import ensure_seed_instruments
from ml import registry


# Affordable names for ₹2,000 (price band) + a few liquid large caps for features/context
BOOTSTRAP_SYMBOLS = [
    "ITC.NS",
    "TATASTEEL.NS",
    "ONGC.NS",
    "NTPC.NS",
    "POWERGRID.NS",
    "COALINDIA.NS",
    "BPCL.NS",
    "HINDALCO.NS",
    "JSWSTEEL.NS",
    "WIPRO.NS",
    "SBIN.NS",
    "AXISBANK.NS",
    "RELIANCE.NS",
    "INFY.NS",
]


class Command(BaseCommand):
    help = "Seed universe; backfill only if needed; train only if needed"

    def add_arguments(self, parser):
        parser.add_argument("--years", type=int, default=3)
        parser.add_argument("--full", action="store_true", help="Optuna search during train")
        parser.add_argument("--skip-backfill", action="store_true")
        parser.add_argument("--force-backfill", action="store_true", help="Always re-download history")
        parser.add_argument("--force-train", action="store_true")

    def handle(self, *args, **options):
        ensure_seed_instruments()
        call_command("migrate", interactive=False, verbosity=0)

        if options["skip_backfill"]:
            self.stdout.write("Backfill skipped (--skip-backfill)")
        elif options["force_backfill"]:
            symbols = ",".join(BOOTSTRAP_SYMBOLS)
            self.stdout.write(f"Force backfill: {len(BOOTSTRAP_SYMBOLS)} symbols...")
            call_command("backfill", years=options["years"], symbols=symbols)
        else:
            status = backfill_status_summary(BOOTSTRAP_SYMBOLS)
            if not status["needed"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Backfill not needed — {status['ok_count']}/{len(BOOTSTRAP_SYMBOLS)} "
                        "symbols already have enough fresh daily bars."
                    )
                )
            else:
                self.stdout.write(
                    f"Backfill needed for {status['need_count']} symbol(s): "
                    f"{', '.join(status['need'][:12])}{'…' if status['need_count'] > 12 else ''}"
                )
                if status["ok_count"]:
                    self.stdout.write(f"Already OK (skip): {status['ok_count']} symbols")
                call_command(
                    "backfill",
                    years=options["years"],
                    symbols=",".join(status["need"]),
                )

        champ = registry.champion("swing_lgbm")
        need_train = options["force_train"] or champ is None
        if champ and not options["force_train"]:
            m = champ.metrics or {}
            if m.get("n_signals", 0) == 0 or m.get("auc", 0) >= 0.99:
                need_train = True
                self.stdout.write("Champion looks overfit/empty — forcing retrain")

        if need_train:
            self.stdout.write("Training with chronological train/test split...")
            call_command("train", mode="swing", full=options["full"], trials=15)
        else:
            self.stdout.write(self.style.SUCCESS(f"Train not needed — champion OK: {champ}"))

        champ = registry.champion("swing_lgbm")
        if champ:
            self.stdout.write(
                self.style.SUCCESS(f"Ready: {champ.name}_v{champ.version}")
            )
        else:
            self.stdout.write(self.style.ERROR("No champion after bootstrap"))
