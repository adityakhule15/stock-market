from django.contrib import admin

from portfolio.models import EquitySnapshot, PortfolioSettings, Position


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("instrument", "side", "qty", "entry_price", "status", "pnl", "mode")


@admin.register(EquitySnapshot)
class EquitySnapshotAdmin(admin.ModelAdmin):
    list_display = ("ts", "equity", "cash", "mode")


@admin.register(PortfolioSettings)
class PortfolioSettingsAdmin(admin.ModelAdmin):
    list_display = ("equity", "cash", "kill_switch", "day_pnl", "week_pnl")
