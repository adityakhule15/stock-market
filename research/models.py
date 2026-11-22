from __future__ import annotations

from django.db import models


class ResearchRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "queued"
        RUNNING = "running", "running"
        OK = "ok", "ok"
        ERROR = "error", "error"
        TIMEOUT = "timeout", "timeout"

    symbol = models.CharField(max_length=32)
    yahoo_ticker = models.CharField(max_length=40)
    as_of = models.DateField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    config_snapshot = models.JSONField(default=dict, blank=True)
    rating = models.CharField(max_length=32, blank=True, default="")
    decision = models.TextField(blank=True, default="")
    report_json = models.JSONField(default=dict, blank=True)
    report_html_path = models.CharField(max_length=512, blank=True, default="")
    report_dir = models.CharField(max_length=512, blank=True, default="")
    cost = models.FloatField(null=True, blank=True)
    tokens = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    signal = models.ForeignKey(
        "signals.Signal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["as_of", "status"]),
            models.Index(fields=["symbol", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.yahoo_ticker} {self.as_of} [{self.status}] {self.rating or '-'}"
