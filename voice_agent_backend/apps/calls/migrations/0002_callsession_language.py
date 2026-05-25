from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("calls", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="callsession",
            name="language",
            field=models.CharField(default="en", max_length=8),
        ),
    ]
