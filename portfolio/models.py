from django.db import models


class Position(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "open"
        CLOSED = "closed", "closed"

    instrument = models.ForeignKey("marketdata.Instrument", on_delete=models.CASCADE)
    signal = models.ForeignKey("signals.Signal", on_delete=models.SET_NULL, null=True, blank=True)
    side = models.CharField(max_length=4, default="BUY")
    qty = models.PositiveIntegerField()
    entry_price = models.DecimalField(max_digits=10, decimal_places=2)
    stop = models.DecimalField(max_digits=10, decimal_places=2)
    target = models.DecimalField(max_digits=10, decimal_places=2)
    # Frozen at suggestion / confirm time for slippage + discipline metrics
    planned_entry = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    planned_stop = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    planned_target = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # [{ts, old_sl, new_sl, old_tgt, new_tgt, source}]
    adjustment_log = models.JSONField(default=list, blank=True)
    last_mgmt_action = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    mode = models.CharField(max_length=10, default="paper")
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    exit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pnl = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)
    # Exact cash locked at open (lot-aware). Used for delete/close restore.
    reserved_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # False = original (counts in wallet / day P&L); True = practice/dummy (excluded from totals)
    is_dummy = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["status", "-opened_at"]),
            models.Index(fields=["is_dummy", "status"]),
        ]


class OrderFill(models.Model):
    """One Groww (or paper) executed leg — BUY or SELL — for the order book / training log."""

    class Side(models.TextChoices):
        BUY = "BUY", "BUY"
        SELL = "SELL", "SELL"

    class Source(models.TextChoices):
        SIGNAL = "signal_confirm", "signal_confirm"
        CLOSE = "close", "close"
        MANUAL = "manual_groww", "manual_groww"

    position = models.ForeignKey(
        Position, on_delete=models.CASCADE, related_name="fills", null=True, blank=True
    )
    instrument = models.ForeignKey("marketdata.Instrument", on_delete=models.CASCADE)
    side = models.CharField(max_length=4, choices=Side.choices)
    product = models.CharField(max_length=8, default="NRML")
    contract = models.CharField(max_length=80, blank=True)  # e.g. NIFTY 26500 CE
    qty = models.PositiveIntegerField(default=1)  # lots (F&O) or shares
    units = models.PositiveIntegerField(default=1)  # tradable units
    price = models.DecimalField(max_digits=12, decimal_places=2)
    turnover = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    charges = models.JSONField(default=dict)  # breakup
    charges_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Wallet effect: BUY negative (premium+charges), SELL positive (premium-charges)
    net_cash_flow = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    broker = models.CharField(max_length=20, default="groww")
    notes = models.CharField(max_length=255, blank=True)
    mistake_note = models.CharField(max_length=255, blank=True)  # for later model retraining
    placed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["-placed_at"]), models.Index(fields=["side", "-placed_at"])]


class EquitySnapshot(models.Model):
    ts = models.DateTimeField(auto_now_add=True)
    equity = models.DecimalField(max_digits=12, decimal_places=2)
    cash = models.DecimalField(max_digits=12, decimal_places=2)
    open_pnl = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mode = models.CharField(max_length=10, default="paper")

    class Meta:
        indexes = [models.Index(fields=["-ts"])]


class PortfolioSettings(models.Model):
    """Singleton-ish row for kill switch and equity tracking."""

    class LifeStatus(models.TextChoices):
        ALIVE = "alive", "alive"
        SURVIVAL = "survival", "survival"
        DEAD = "dead", "dead"

    kill_switch = models.BooleanField(default=False)
    life_status = models.CharField(
        max_length=10, choices=LifeStatus.choices, default=LifeStatus.ALIVE
    )
    equity = models.DecimalField(max_digits=12, decimal_places=2, default=2000)
    cash = models.DecimalField(max_digits=12, decimal_places=2, default=2000)
    start_equity = models.DecimalField(max_digits=12, decimal_places=2, default=2000)
    day_pnl = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    week_pnl = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    day_key = models.DateField(null=True, blank=True)
    week_key = models.CharField(max_length=8, blank=True)  # YYYY-Www
    # Cooling-off after consecutive losses
    consecutive_losses = models.PositiveSmallIntegerField(default=0)
    lockout_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    # F&O underlyings always analyzed from Signals (multi-select in Settings)
    fno_watchlist = models.JSONField(default=list, blank=True)
    fno_auto_analyze = models.BooleanField(default=False)
    # Positions LTP poll interval (seconds); browser + live hub
    live_refresh_sec = models.PositiveSmallIntegerField(default=10)
    # INDstocks JWT — preferred over .env until JWT exp; cleared on expiry
    indstocks_access_token = models.TextField(blank=True, default="")
    indstocks_token_exp = models.DateTimeField(null=True, blank=True)
    # TradingAgents UI overrides (enabled, provider, models, rounds, top_n, caps)
    agents_config = models.JSONField(default=dict, blank=True)

    @classmethod
    def get_solo(cls) -> "PortfolioSettings":
        obj = cls.objects.first()
        if obj is None:
            from config import trading_config as cfg

            obj = cls.objects.create(
                equity=cfg.CAPITAL,
                cash=cfg.CAPITAL,
                start_equity=cfg.CAPITAL,
                fno_watchlist=["NIFTY", "BANKNIFTY"],
            )
        return obj


class TradeReview(models.Model):
    """Forced post-close review for mistake learning / retrain."""

    class ExitReason(models.TextChoices):
        TARGET = "target", "target"
        SL = "sl", "sl"
        MANUAL_EARLY = "manual_early", "manual_early"
        MANUAL_LATE = "manual_late", "manual_late"
        TIME = "time", "time"

    position = models.OneToOneField(
        Position, on_delete=models.CASCADE, related_name="review"
    )
    exit_reason = models.CharField(max_length=20, choices=ExitReason.choices)
    mistake_tags = models.JSONField(default=list, blank=True)
    note = models.TextField(blank=True, default="")
    planned_vs_actual = models.JSONField(default=dict, blank=True)
    followed_plan = models.BooleanField(default=False)
    regime_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["-created_at"])]
