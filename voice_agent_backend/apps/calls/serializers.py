from rest_framework import serializers

from apps.calls.models import CallEvent, CallSession


class StartCallSerializer(serializers.Serializer):
    persona_id = serializers.CharField(required=False, allow_blank=True, default="")
    system_prompt = serializers.CharField(required=False, allow_blank=True, default="")


class EndCallSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class CallEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallEvent
        fields = ["event_type", "payload", "created_at"]


class CallSessionDetailSerializer(serializers.ModelSerializer):
    events = CallEventSerializer(many=True, read_only=True)

    class Meta:
        model = CallSession
        fields = [
            "id",
            "room_name",
            "user_identity",
            "status",
            "persona_id",
            "created_at",
            "ended_at",
            "end_reason",
            "events",
        ]
