from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portfolio", "0002_portfoliosettings_life_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="position",
            name="reserved_cash",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="portfoliosettings",
            name="fno_watchlist",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="portfoliosettings",
            name="fno_auto_analyze",
            field=models.BooleanField(default=False),
        ),
    ]
