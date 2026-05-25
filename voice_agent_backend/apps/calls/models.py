import uuid

from django.db import models
from django.utils import timezone


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
