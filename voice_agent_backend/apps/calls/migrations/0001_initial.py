import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="CallSession",
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
                ("room_name", models.CharField(db_index=True, max_length=128, unique=True)),
                ("user_identity", models.CharField(max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("created", "Created"),
                            ("active", "Active"),
                            ("ended", "Ended"),
                            ("failed", "Failed"),
                        ],
                        default="created",
                        max_length=32,
                    ),
                ),
                ("persona_id", models.CharField(blank=True, default="", max_length=64)),
                ("system_prompt", models.TextField(blank=True, default="")),
                ("token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("end_reason", models.CharField(blank=True, default="", max_length=255)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CallEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("event_type", models.CharField(db_index=True, max_length=64)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="calls.callsession",
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
