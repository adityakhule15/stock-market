from django.core.management.base import BaseCommand

from marketdata.scheduler import start_blocking


class Command(BaseCommand):
    help = "APScheduler worker only (prefer: python manage.py run for web+jobs together)"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Worker started (Asia/Kolkata). Ctrl+C to stop."))
        start_blocking()
