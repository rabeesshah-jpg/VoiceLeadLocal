from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.calls.models import CallEvent, CallSession, Lead, VoiceProfile


class CallEventInline(admin.TabularInline):
    model = CallEvent
    extra = 0
    fields = ("event_type", "short_payload", "created_at")
    readonly_fields = ("event_type", "short_payload", "created_at")
    can_delete = False
    ordering = ("created_at",)

    def short_payload(self, obj):
        text = obj.payload.get("text") if isinstance(obj.payload, dict) else None
        if text:
            preview = text[:120] + ("…" if len(text) > 120 else "")
            return preview
        return str(obj.payload)[:120]

    short_payload.short_description = "Payload"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CallSession)
class CallSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "room_name",
        "status",
        "persona_id",
        "language",
        "created_at",
        "ended_at",
    )
    list_filter = ("status", "language", "voice_mode")
    search_fields = ("id", "room_name", "user_identity", "persona_id")
    readonly_fields = (
        "id",
        "room_name",
        "user_identity",
        "created_at",
        "updated_at",
        "ended_at",
        "transcript_view",
    )
    fields = (
        "id",
        "room_name",
        "user_identity",
        "status",
        "persona_id",
        "voice_mode",
        "language",
        "system_prompt",
        "created_at",
        "updated_at",
        "ended_at",
        "end_reason",
        "transcript_view",
    )
    inlines = [CallEventInline]
    ordering = ("-created_at",)

    def transcript_view(self, obj):
        events = obj.events.filter(
            event_type__in=["user_transcript", "agent_response"]
        ).order_by("created_at")

        if not events:
            return "No transcript recorded for this call."

        rows = []
        for e in events:
            text = e.payload.get("text", "") if isinstance(e.payload, dict) else ""
            if not text:
                continue
            speaker = "Caller" if e.event_type == "user_transcript" else "Noura"
            align = "left" if speaker == "Caller" else "right"
            bg = "#eef2ff" if speaker == "Caller" else "#e6f9f0"
            rows.append(
                f'<div style="text-align:{align}; margin:6px 0;">'
                f'<span style="display:inline-block; max-width:70%; padding:8px 12px; '
                f'border-radius:10px; background:{bg}; text-align:left;">'
                f'<strong>{speaker}:</strong><br>{text}'
                f"</span></div>"
            )

        html = (
            '<div style="max-height:500px; overflow-y:auto; padding:12px; '
            'border:1px solid #ddd; border-radius:8px; background:#fafafa;">'
            + "".join(rows)
            + "</div>"
        )
        return mark_safe(html)

    transcript_view.short_description = "Conversation transcript"


@admin.register(CallEvent)
class CallEventAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "event_type", "created_at")
    list_filter = ("event_type",)
    search_fields = ("session__room_name", "session__id")
    readonly_fields = ("session", "event_type", "payload", "created_at")
    ordering = ("-created_at",)


@admin.register(VoiceProfile)
class VoiceProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "provider", "is_default", "created_at")
    list_filter = ("status", "provider", "source_type")
    search_fields = ("name", "id")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "company",
        "whatsapp_number",
        "city",
        "lead_intent",
        "appointment_time",
        "created_at",
    )
    list_filter = ("lead_intent", "appointment_confirmed")
    search_fields = ("name", "company", "whatsapp_number", "city")
    readonly_fields = ("id", "session", "created_at", "updated_at")