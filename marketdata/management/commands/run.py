"""One command: migrate + collectstatic + embedded scheduler + web server."""

from __future__ import annotations

import os
import subprocess
import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start StockScope in one process: migrations, static, scheduler + web (port 8001)"

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8001")))
        parser.add_argument("--skip-migrate", action="store_true")
        parser.add_argument("--no-scheduler", action="store_true")
        parser.add_argument("--bootstrap", action="store_true", help="Backfill+train if needed before start")

    def handle(self, *args, **options):
        if not options["skip_migrate"]:
            self.stdout.write("Running migrations...")
            call_command("migrate", interactive=False, verbosity=1)
        try:
            call_command("collectstatic", interactive=False, verbosity=0, clear=False)
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"collectstatic skipped: {exc}"))

        if options["bootstrap"]:
            self.stdout.write("Bootstrap (data + model)...")
            call_command("bootstrap", force_train=False)

        # Auto-bootstrap lightly if no champion
        try:
            from ml import registry

            if registry.champion("swing_lgbm") is None:
                self.stdout.write("No champion — running bootstrap...")
                call_command("bootstrap")
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"bootstrap check: {exc}"))

        if options["no_scheduler"]:
            os.environ["DISABLE_SCHEDULER"] = "1"
        else:
            os.environ["RUN_EMBEDDED_SCHEDULER"] = "1"
            from marketdata.scheduler import start_embedded

            start_embedded()
            self.stdout.write(self.style.SUCCESS("Scheduler embedded (background jobs ON)"))

        port = options["port"]
        use_gunicorn = os.environ.get("USE_GUNICORN", "0") == "1" or os.environ.get("RENDER") or os.environ.get(
            "RAILWAY_ENVIRONMENT"
        )
        if use_gunicorn:
            self.stdout.write(self.style.SUCCESS(f"Starting gunicorn on 0.0.0.0:{port}"))
            cmd = [
                sys.executable,
                "-m",
                "gunicorn",
                "config.wsgi:application",
                "--bind",
                f"0.0.0.0:{port}",
                "--workers",
                os.environ.get("WEB_CONCURRENCY", "1"),
                "--timeout",
                "120",
            ]
            raise SystemExit(subprocess.call(cmd))
        self.stdout.write(self.style.SUCCESS(f"Starting StockScope on http://127.0.0.1:{port}"))
        call_command("runserver", f"0.0.0.0:{port}", use_reloader=False)
