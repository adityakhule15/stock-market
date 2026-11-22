from __future__ import annotations

from django.db import models


class KronosForecast(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "queued"
        RUNNING = "running", "running"
        OK = "ok", "ok"
        ERROR = "error", "error"
        TIMEOUT = "timeout", "timeout"

    symbol = models.CharField(max_length=32, db_index=True)
    as_of = models.DateField(db_index=True)
    interval = models.CharField(max_length=8, default="1d")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    model_id = models.CharField(max_length=128, blank=True, default="")
    lookback = models.PositiveIntegerField(default=0)
    pred_len = models.PositiveIntegerField(default=5)
    paths_n = models.PositiveIntegerField(default=0)
    config_snapshot = models.JSONField(default=dict, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    call = models.CharField(max_length=16, blank=True, default="")  # BUY/NEUTRAL/AVOID
    error = models.TextField(blank=True, default="")
    entry = models.FloatField(null=True, blank=True)
    stop = models.FloatField(null=True, blank=True)
    target = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["symbol", "as_of", "interval"]),
            models.Index(fields=["as_of", "status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.symbol} {self.as_of} {self.interval} [{self.status}] {self.call or '-'}"
