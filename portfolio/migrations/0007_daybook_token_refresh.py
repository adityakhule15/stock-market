from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio", "0006_pipeline_wallet_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfoliosettings",
            name="live_refresh_sec",
            field=models.PositiveSmallIntegerField(default=10),
        ),
        migrations.AddField(
            model_name="portfoliosettings",
            name="indstocks_access_token",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="portfoliosettings",
            name="indstocks_token_exp",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
