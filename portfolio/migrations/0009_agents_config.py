from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portfolio", "0008_position_is_dummy"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfoliosettings",
            name="agents_config",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
