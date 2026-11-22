from django.db import models


class Instrument(models.Model):
    symbol = models.CharField(max_length=32, unique=True)  # e.g. RELIANCE.NS
    name = models.CharField(max_length=128, blank=True)
    exchange = models.CharField(max_length=8, default="NSE")
    is_active = models.BooleanField(default=True)
    sector = models.CharField(max_length=64, blank=True)
    in_asm_gsm = models.BooleanField(default=False)
    next_result_date = models.DateField(null=True, blank=True)
    avg_traded_value_20d = models.FloatField(default=0.0)
    avg_volume_20d = models.FloatField(default=0.0)
    last_price = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.symbol


class Bar(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="bars")
    interval = models.CharField(max_length=8)  # 1d, 5m, 1m
    ts = models.DateTimeField()
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    volume = models.FloatField(default=0.0)
    source = models.CharField(max_length=20, default="yfinance")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "interval", "ts"],
                name="uniq_bar_instrument_interval_ts",
            )
        ]
        indexes = [
            models.Index(fields=["instrument", "interval", "-ts"]),
        ]

    def __str__(self) -> str:
        return f"{self.instrument.symbol} {self.interval} {self.ts}"


class JobRun(models.Model):
    name = models.CharField(max_length=64)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    detail = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["name", "-started_at"])]
