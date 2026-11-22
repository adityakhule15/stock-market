from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("signals", "0002_pipeline_wallet_review"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShadowMark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("underlying", models.CharField(max_length=32)),
                ("option_type", models.CharField(max_length=2)),
                ("strike", models.FloatField()),
                ("expiry", models.CharField(blank=True, default="", max_length=20)),
                ("ltp", models.FloatField()),
                ("bid", models.FloatField(blank=True, null=True)),
                ("ask", models.FloatField(blank=True, null=True)),
                ("ts", models.DateTimeField(auto_now_add=True)),
                ("source", models.CharField(blank=True, default="", max_length=32)),
            ],
        ),
        migrations.CreateModel(
            name="ShadowTrade",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("stocks", "stocks"), ("options", "options")], max_length=12)),
                ("symbol", models.CharField(max_length=40)),
                ("would_recommend", models.BooleanField(default=False)),
                ("session_date", models.DateField()),
                ("levels", models.JSONField(default=dict)),
                ("probability", models.FloatField(blank=True, null=True)),
                ("score", models.FloatField(blank=True, null=True)),
                ("model_votes", models.JSONField(blank=True, default=dict)),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("open", "open"),
                            ("target", "target"),
                            ("stop", "stop"),
                            ("time", "time"),
                            ("adverse", "adverse"),
                            ("no_trigger", "no_trigger"),
                            ("no_data", "no_data"),
                        ],
                        default="open",
                        max_length=16,
                    ),
                ),
                ("gross_pnl", models.FloatField(default=0)),
                ("charges", models.FloatField(default=0)),
                ("net_pnl", models.FloatField(default=0)),
                ("exit_reason", models.CharField(blank=True, default="", max_length=32)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("meta", models.JSONField(blank=True, default=dict)),
                (
                    "signal",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shadow_trades",
                        to="signals.signal",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="shadowmark",
            index=models.Index(fields=["underlying", "option_type", "strike", "-ts"], name="evidence_sh_underly_idx"),
        ),
        migrations.AddIndex(
            model_name="shadowtrade",
            index=models.Index(fields=["kind", "outcome", "-session_date"], name="evidence_sh_kind_ou_idx"),
        ),
        migrations.AddIndex(
            model_name="shadowtrade",
            index=models.Index(
                fields=["would_recommend", "kind", "outcome"], name="evidence_sh_would_r_idx"
            ),
        ),
    ]
