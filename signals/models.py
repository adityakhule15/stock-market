from django.db import models


class Signal(models.Model):
    class Side(models.TextChoices):
        BUY = "BUY", "BUY"
        SELL = "SELL", "SELL"

    class Status(models.TextChoices):
        NEW = "new", "new"
        TRIGGERED = "triggered", "triggered"
        EXPIRED = "expired", "expired"
        STOPPED = "stopped", "stopped"
        TARGET = "target", "target"
        CLOSED = "closed", "closed"
        SKIPPED = "skipped", "skipped"

    class EntryStatus(models.TextChoices):
        WAITING = "waiting", "waiting"
        TRIGGERED = "triggered", "triggered"
        EXPIRED = "expired", "expired"

    instrument = models.ForeignKey("marketdata.Instrument", on_delete=models.CASCADE)
    side = models.CharField(max_length=4, choices=Side.choices)
    strategy = models.CharField(max_length=20, default="swing")
    product = models.CharField(max_length=4, default="CNC")
    created_at = models.DateTimeField(auto_now_add=True)
    data_as_of = models.DateTimeField()
    data_source = models.CharField(max_length=20)
    valid_until = models.DateTimeField()

    entry_trigger = models.DecimalField(max_digits=10, decimal_places=2)
    entry_limit = models.DecimalField(max_digits=10, decimal_places=2)
    sl_trigger = models.DecimalField(max_digits=10, decimal_places=2)
    sl_limit = models.DecimalField(max_digits=10, decimal_places=2)
    target1 = models.DecimalField(max_digits=10, decimal_places=2)
    target2 = models.DecimalField(max_digits=10, decimal_places=2)
    qty = models.PositiveIntegerField()
    capital_used = models.DecimalField(max_digits=10, decimal_places=2)
    max_loss = models.DecimalField(max_digits=10, decimal_places=2)
    rr_net = models.FloatField()
    probability = models.FloatField()
    model_version = models.ForeignKey("ml.ModelVersion", on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    entry_status = models.CharField(
        max_length=12, choices=EntryStatus.choices, default=EntryStatus.WAITING
    )
    chase_limit = models.DecimalField(max_digits=10, decimal_places=2)
    data_age_min = models.PositiveSmallIntegerField(default=0)
    time_machine = models.JSONField(default=dict)
    gate = models.JSONField(default=dict)
    reasons = models.JSONField(default=dict)
    # Nine-stage day-trader pipeline outputs (scanner…alerts)
    pipeline = models.JSONField(default=dict, blank=True)
    mode = models.CharField(max_length=10, default="paper")  # paper | live
    skip_reason = models.CharField(max_length=255, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "-created_at"])]

    def planned_levels(self) -> dict:
        """Frozen suggestion levels for planned-vs-actual tracking."""
        return {
            "entry": float(self.entry_trigger),
            "stop": float(self.sl_trigger),
            "target": float(self.target1),
            "qty": int(self.qty),
        }

    def __str__(self) -> str:
        return f"{self.side} {self.instrument.symbol} @ {self.entry_trigger}"
