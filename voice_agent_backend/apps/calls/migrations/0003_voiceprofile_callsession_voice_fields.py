import uuid

import django.db.models.deletion
from django.db import migrations, models

import apps.calls.models


class Migration(migrations.Migration):
    dependencies = [
        ("calls", "0002_callsession_language"),
    ]

    operations = [
        migrations.CreateModel(
            name="VoiceProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                (
                    "provider",
                    models.CharField(default="runpod_supertonic", max_length=64),
                ),
                (
                    "provider_voice_id",
                    models.CharField(blank=True, default="", max_length=256),
                ),
                (
                    "runpod_voice_uuid",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "reference_audio_file",
                    models.FileField(
                        blank=True,
                        null=True,
                        upload_to=apps.calls.models.voice_profile_upload_to,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="callsession",
            name="fallback_voice",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="callsession",
            name="provider_voice_id",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AddField(
            model_name="callsession",
            name="voice_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="call_sessions",
                to="calls.voiceprofile",
            ),
        ),
    ]
