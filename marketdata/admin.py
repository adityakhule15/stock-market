from django.contrib import admin

from marketdata.models import Bar, Instrument, JobRun


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "last_price", "is_active", "in_asm_gsm")
    search_fields = ("symbol", "name")


@admin.register(Bar)
class BarAdmin(admin.ModelAdmin):
    list_display = ("instrument", "interval", "ts", "close", "source")
    list_filter = ("interval", "source")


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = ("name", "started_at", "finished_at", "ok")
