from django.apps import AppConfig


class MarketdataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "marketdata"

    def ready(self):
        # Avoid double-start under autoreload parent process
        import os
        import sys

        if os.environ.get("RUN_MAIN") == "false":
            return
        if "migrate" in sys.argv or "makemigrations" in sys.argv or "test" in sys.argv:
            return
        if os.environ.get("RUN_EMBEDDED_SCHEDULER") == "1" and os.environ.get("DISABLE_SCHEDULER", "") != "1":
            try:
                from marketdata.scheduler import start_embedded

                start_embedded()
            except Exception:  # noqa: BLE001
                pass
