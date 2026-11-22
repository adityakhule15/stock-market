"""Prepare data + model so desktop launch is ready.

Backfill runs ONLY when history is missing, too short, or stale.
Train runs when there is no champion, the champion is legacy (pre-honest),
or metrics look empty/overfit. Use --force-train to always retrain.
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
    "PNB.NS",
    "NMDC.NS",
    "SAIL.NS",
    "TATAPOWER.NS",
]


def _champion_needs_train(champ) -> tuple[bool, str]:
    if champ is None:
        return True, "no champion model"
    m = champ.metrics if isinstance(champ.metrics, dict) else {}
    if not m.get("honest"):
        return True, "legacy in-sample champion — need honest retrain"
    if float(m.get("auc") or 0) >= 0.99 and not m.get("provisional"):
        return True, "champion looks overfit (AUC ≥ 0.99)"
    # Stale: no holdout metrics after overhaul
    if m.get("holdout") is None and m.get("expectancy_R") is None and not m.get("honest"):
        return True, "champion missing honest holdout metrics"
    # Honest (even provisional / no edge) is enough for desktop — weekly job retrains
    if m.get("provisional") or m.get("no_trustworthy_model"):
        return False, "honest provisional champion (no proven edge yet — OK for launch)"
    return False, "champion OK"


class Command(BaseCommand):
    help = "Seed universe; migrate; backfill if needed; train/update model if needed"

    def add_arguments(self, parser):
        parser.add_argument("--years", type=int, default=3)
        parser.add_argument("--full", action="store_true", help="Optuna search during train")
        parser.add_argument("--skip-backfill", action="store_true")
        parser.add_argument("--force-backfill", action="store_true", help="Always re-download history")
        parser.add_argument("--force-train", action="store_true")
        parser.add_argument(
            "--skip-train",
            action="store_true",
            help="Never train (migrate/backfill only)",
        )

    def handle(self, *args, **options):
        self.stdout.write("1/4 Migrate database…")
        ensure_seed_instruments()
        call_command("migrate", interactive=False, verbosity=0)
        self.stdout.write(self.style.SUCCESS("   Migrations OK"))

        if options["skip_backfill"]:
            self.stdout.write("2/4 Backfill skipped (--skip-backfill)")
        elif options["force_backfill"]:
            self.stdout.write(f"2/4 Force backfill: {len(BOOTSTRAP_SYMBOLS)} symbols…")
            call_command("backfill", years=options["years"], symbols=",".join(BOOTSTRAP_SYMBOLS))
        else:
            status = backfill_status_summary(BOOTSTRAP_SYMBOLS)
            if not status["needed"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"2/4 Backfill not needed — {status['ok_count']}/{len(BOOTSTRAP_SYMBOLS)} "
                        "symbols already fresh."
                    )
                )
            else:
                self.stdout.write(
                    f"2/4 Backfill needed for {status['need_count']} symbol(s): "
                    f"{', '.join(status['need'][:12])}{'…' if status['need_count'] > 12 else ''}"
                )
                if status["ok_count"]:
                    self.stdout.write(f"   Already OK (skip): {status['ok_count']} symbols")
                call_command(
                    "backfill",
                    years=options["years"],
                    symbols=",".join(status["need"]),
                )

        champ = registry.champion("swing_lgbm")
        need_train, reason = _champion_needs_train(champ)
        if options["force_train"]:
            need_train, reason = True, "forced by --force-train"
        if options["skip_train"]:
            need_train, reason = False, "skipped (--skip-train)"

        if need_train:
            self.stdout.write(f"3/4 Training model ({reason})…")
            # Honest train: retire legacy champions on first honest run
            call_command(
                "train",
                mode="swing",
                full=options["full"],
                trials=15 if options["full"] else 10,
                retire_legacy=True,
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"3/4 Train not needed — {reason}: {champ}"))

        champ = registry.champion("swing_lgbm")
        if champ:
            m = champ.metrics if isinstance(champ.metrics, dict) else {}
            honest = "honest" if m.get("honest") else "legacy"
            self.stdout.write(
                self.style.SUCCESS(
                    f"4/4 Ready: {champ.name}_v{champ.version} ({honest}) "
                    f"status={champ.status}"
                )
            )
        else:
            self.stdout.write(self.style.ERROR("4/4 No champion after bootstrap — scans will be empty"))
