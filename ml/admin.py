from django.contrib import admin

from ml.models import DriftReport, ModelVersion


@admin.register(ModelVersion)
class ModelVersionAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "status", "algo", "created_at")
    list_filter = ("status", "name")


@admin.register(DriftReport)
class DriftReportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "model_name", "psi_max", "triggered_retrain")
