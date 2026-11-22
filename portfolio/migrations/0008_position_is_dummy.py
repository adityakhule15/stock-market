from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio", "0007_daybook_token_refresh"),
    ]

    operations = [
        migrations.AddField(
            model_name="position",
            name="is_dummy",
            field=models.BooleanField(default=False),
        ),
        migrations.AddIndex(
            model_name="position",
            index=models.Index(fields=["is_dummy", "status"], name="portfolio_p_is_dumm_idx"),
        ),
    ]
