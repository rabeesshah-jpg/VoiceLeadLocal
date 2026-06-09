from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("calls", "0003_voiceprofile_callsession_voice_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="callsession",
            name="voice_mode",
            field=models.CharField(blank=True, default="preset", max_length=16),
        ),
    ]
