from __future__ import annotations

from django.db import models


class ShadowTrade(models.Model):
    class Kind(models.TextChoices):
        STOCKS = "stocks", "stocks"
        OPTIONS = "options", "options"

    class Outcome(models.TextChoices):
        OPEN = "open", "open"
        TARGET = "target", "target"
        STOP = "stop", "stop"
        TIME = "time", "time"
        ADVERSE = "adverse", "adverse"
        NO_TRIGGER = "no_trigger", "no_trigger"
        NO_DATA = "no_data", "no_data"

    signal = models.ForeignKey(
        "signals.Signal", on_delete=models.SET_NULL, null=True, blank=True, related_name="shadow_trades"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    symbol = models.CharField(max_length=40)
    would_recommend = models.BooleanField(default=False)
    session_date = models.DateField()
    levels = models.JSONField(default=dict)  # entry/stop/target/qty/lot
    probability = models.FloatField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    model_votes = models.JSONField(default=dict, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.OPEN)
    gross_pnl = models.FloatField(default=0)
    charges = models.FloatField(default=0)
    net_pnl = models.FloatField(default=0)
    exit_reason = models.CharField(max_length=32, blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["kind", "outcome", "-session_date"]),
            models.Index(fields=["would_recommend", "kind", "outcome"]),
        ]

    def __str__(self) -> str:
        return f"Shadow {self.kind} {self.symbol} {self.outcome} net={self.net_pnl}"


class ShadowMark(models.Model):
    """Intraday option LTP snapshots for resolving same-day practice trades."""

    underlying = models.CharField(max_length=32)
    option_type = models.CharField(max_length=2)  # CE / PE
    strike = models.FloatField()
    expiry = models.CharField(max_length=20, blank=True, default="")
    ltp = models.FloatField()
    bid = models.FloatField(null=True, blank=True)
    ask = models.FloatField(null=True, blank=True)
    ts = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["underlying", "option_type", "strike", "-ts"]),
        ]
