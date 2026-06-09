import uuid

from django.db import models
from django.utils import timezone


def voice_profile_upload_to(instance, filename: str) -> str:
    return f"voice_profiles/{instance.id}/{filename}"


class VoiceProfile(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
    ]

    PROVIDER_RUNPOD_SUPERTONIC = "runpod_supertonic"

    SOURCE_REFERENCE_AUDIO = "reference_audio"
    SOURCE_VOICE_BUILDER_JSON = "voice_builder_json"
    SOURCE_PRESET = "preset"
    SOURCE_UNKNOWN = "unknown"

    SOURCE_TYPE_CHOICES = [
        (SOURCE_REFERENCE_AUDIO, "Reference audio"),
        (SOURCE_VOICE_BUILDER_JSON, "Voice Builder JSON"),
        (SOURCE_PRESET, "Preset"),
        (SOURCE_UNKNOWN, "Unknown"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    provider = models.CharField(max_length=64, default=PROVIDER_RUNPOD_SUPERTONIC)
    provider_voice_id = models.CharField(max_length=256, blank=True, default="")
    runpod_voice_uuid = models.CharField(max_length=64, blank=True, default="")
    source_type = models.CharField(
        max_length=32,
        choices=SOURCE_TYPE_CHOICES,
        default=SOURCE_UNKNOWN,
    )
    reference_audio_file = models.FileField(
        upload_to=voice_profile_upload_to, blank=True, null=True
    )
    error_message = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"


class CallSession(models.Model):
    STATUS_CREATED = "created"
    STATUS_ACTIVE = "active"
    STATUS_ENDED = "ended"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ENDED, "Ended"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room_name = models.CharField(max_length=128, unique=True, db_index=True)
    user_identity = models.CharField(max_length=128)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_CREATED)
    persona_id = models.CharField(max_length=64, blank=True, default="")
    voice_mode = models.CharField(max_length=16, blank=True, default="preset")
    voice_profile = models.ForeignKey(
        VoiceProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="call_sessions",
    )
    provider_voice_id = models.CharField(max_length=256, blank=True, default="")
    fallback_voice = models.CharField(max_length=64, blank=True, default="")
    language = models.CharField(max_length=8, default="en")
    system_prompt = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def mark_active(self):
        self.status = self.STATUS_ACTIVE
        self.save(update_fields=["status", "updated_at"])

    def mark_ended(self, reason: str = ""):
        self.status = self.STATUS_ENDED
        self.ended_at = timezone.now()
        self.end_reason = reason or ""
        self.save(update_fields=["status", "ended_at", "end_reason", "updated_at"])


class CallEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
