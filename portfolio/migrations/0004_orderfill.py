from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("marketdata", "0001_initial"),
        ("portfolio", "0003_fno_watchlist_reserved_cash"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderFill",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("side", models.CharField(choices=[("BUY", "BUY"), ("SELL", "SELL")], max_length=4)),
                ("product", models.CharField(default="NRML", max_length=8)),
                ("contract", models.CharField(blank=True, max_length=80)),
                ("qty", models.PositiveIntegerField(default=1)),
                ("units", models.PositiveIntegerField(default=1)),
                ("price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("turnover", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("charges", models.JSONField(default=dict)),
                ("charges_total", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("net_cash_flow", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("signal_confirm", "signal_confirm"),
                            ("close", "close"),
                            ("manual_groww", "manual_groww"),
                        ],
                        default="manual_groww",
                        max_length=20,
                    ),
                ),
                ("broker", models.CharField(default="groww", max_length=20)),
                ("notes", models.CharField(blank=True, max_length=255)),
                ("mistake_note", models.CharField(blank=True, max_length=255)),
                ("placed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "instrument",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="marketdata.instrument"),
                ),
                (
                    "position",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fills",
                        to="portfolio.position",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="orderfill",
            index=models.Index(fields=["-placed_at"], name="portfolio_o_placed__7a0e1a_idx"),
        ),
        migrations.AddIndex(
            model_name="orderfill",
            index=models.Index(fields=["side", "-placed_at"], name="portfolio_o_side_9c2a1b_idx"),
        ),
    ]
