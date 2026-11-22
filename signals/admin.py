from django.contrib import admin

from signals.models import Signal


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = ("instrument", "side", "status", "probability", "rr_net", "created_at")
    list_filter = ("status", "side", "strategy")
