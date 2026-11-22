from django.contrib import admin

from forecast.models import KronosForecast


@admin.register(KronosForecast)
class KronosForecastAdmin(admin.ModelAdmin):
    list_display = (
        "symbol",
        "as_of",
        "interval",
        "status",
        "call",
        "paths_n",
        "model_id",
        "finished_at",
    )
    list_filter = ("status", "interval", "call")
    search_fields = ("symbol", "model_id", "error")
