from django.contrib import admin

from indexdesk.models import IndexPatternSnapshot, IndexTrainRun


@admin.register(IndexPatternSnapshot)
class IndexPatternSnapshotAdmin(admin.ModelAdmin):
    list_display = ("underlying", "session_date", "bias", "spot", "ts")
    list_filter = ("underlying", "bias", "session_date")


@admin.register(IndexTrainRun)
class IndexTrainRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "ok", "version", "promoted", "detail")
