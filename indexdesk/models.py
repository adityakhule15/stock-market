from __future__ import annotations

from django.db import models


class IndexPatternSnapshot(models.Model):
    """Latest (and historical) pattern/probability snapshot for an index."""

    underlying = models.CharField(max_length=16)  # NIFTY | BANKNIFTY
    session_date = models.DateField()
    ts = models.DateTimeField(auto_now_add=True)
    spot = models.FloatField(default=0.0)
    spot_source = models.CharField(max_length=32, blank=True, default="")
    patterns = models.JSONField(default=list, blank=True)
    model_prob_up = models.FloatField(null=True, blank=True)
    model_prob_down = models.FloatField(null=True, blank=True)
    bias = models.CharField(max_length=16, blank=True, default="NEUTRAL")  # BULLISH|BEARISH|NEUTRAL
    call = models.JSONField(default=dict, blank=True)  # sized F&O ticket suggestion
    position_advice = models.JSONField(default=dict, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["underlying", "-ts"]),
            models.Index(fields=["underlying", "session_date", "-ts"]),
        ]

    def __str__(self) -> str:
        return f"{self.underlying} {self.session_date} {self.bias} @ {self.spot}"


class IndexTrainRun(models.Model):
    """Audit log for index_pattern_lgbm retrain runs."""

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    version = models.IntegerField(null=True, blank=True)
    promoted = models.BooleanField(default=False)
    detail = models.TextField(blank=True, default="")
    metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["-started_at"])]
