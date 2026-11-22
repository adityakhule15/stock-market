from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("signals", "0002_pipeline_wallet_review"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("symbol", models.CharField(max_length=32)),
                ("yahoo_ticker", models.CharField(max_length=40)),
                ("as_of", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "queued"),
                            ("running", "running"),
                            ("ok", "ok"),
                            ("error", "error"),
                            ("timeout", "timeout"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=12,
                    ),
                ),
                ("config_snapshot", models.JSONField(blank=True, default=dict)),
                ("rating", models.CharField(blank=True, default="", max_length=32)),
                ("decision", models.TextField(blank=True, default="")),
                ("report_json", models.JSONField(blank=True, default=dict)),
                ("report_html_path", models.CharField(blank=True, default="", max_length=512)),
                ("report_dir", models.CharField(blank=True, default="", max_length=512)),
                ("cost", models.FloatField(blank=True, null=True)),
                ("tokens", models.PositiveIntegerField(blank=True, null=True)),
                ("error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "signal",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="research_runs",
                        to="signals.signal",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="researchrun",
            index=models.Index(fields=["as_of", "status"], name="research_re_as_of_6c3e0a_idx"),
        ),
        migrations.AddIndex(
            model_name="researchrun",
            index=models.Index(fields=["symbol", "-created_at"], name="research_re_symbol_3e8f2a_idx"),
        ),
    ]
