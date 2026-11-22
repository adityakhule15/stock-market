import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

# One-process local/run: start jobs alongside the web app
if os.environ.get("RUN_EMBEDDED_SCHEDULER", "1") == "1" and os.environ.get("DISABLE_SCHEDULER", "") != "1":
    try:
        from marketdata.scheduler import start_embedded

        start_embedded()
    except Exception:  # noqa: BLE001
        pass
