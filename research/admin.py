from django.contrib import admin

from research.models import ResearchRun


@admin.register(ResearchRun)
class ResearchRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "symbol",
        "yahoo_ticker",
        "as_of",
        "status",
        "rating",
        "created_at",
    )
    list_filter = ("status", "rating", "as_of")
    search_fields = ("symbol", "yahoo_ticker", "error")
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")
