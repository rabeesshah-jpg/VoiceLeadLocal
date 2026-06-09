from django.db import migrations, models


def backfill_source_type(apps, schema_editor):
    VoiceProfile = apps.get_model("calls", "VoiceProfile")
    for profile in VoiceProfile.objects.all():
        if profile.source_type and profile.source_type != "unknown":
            continue
        if profile.reference_audio_file:
            name = (profile.reference_audio_file.name or "").lower()
            if name.endswith(".json"):
                profile.source_type = "voice_builder_json"
            else:
                profile.source_type = "reference_audio"
        else:
            profile.source_type = "unknown"
        profile.save(update_fields=["source_type"])


class Migration(migrations.Migration):
    dependencies = [
        ("calls", "0004_callsession_voice_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="voiceprofile",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("reference_audio", "Reference audio"),
                    ("voice_builder_json", "Voice Builder JSON"),
                    ("preset", "Preset"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_source_type, migrations.RunPython.noop),
    ]
