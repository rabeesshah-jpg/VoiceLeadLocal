from rest_framework import serializers

from apps.calls.models import CallEvent, CallSession, VoiceProfile


class StartCallSerializer(serializers.Serializer):
    persona_id = serializers.ChoiceField(
        choices=["male", "female", ""],
        required=False,
        allow_blank=True,
        default="",
        help_text="Voice preset: male → M1, female → F1",
    )
    language = serializers.ChoiceField(
        choices=["en", "ar"],
        required=False,
        default="en",
        help_text="Conversation language: en (English) or ar (Arabic)",
    )
    system_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    voice_mode = serializers.ChoiceField(
        choices=["preset", "custom"],
        required=False,
        default="preset",
    )
    voice_profile_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    fallback_voice = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=64
    )

    def validate(self, attrs):
        voice_mode = attrs.get("voice_mode", "preset")
        voice_profile_id = attrs.get("voice_profile_id")
        if voice_mode == "custom" and not voice_profile_id:
            raise serializers.ValidationError(
                {"voice_profile_id": "Required when voice_mode is custom."}
            )
        if voice_mode == "preset":
            attrs["voice_profile_id"] = None
        return attrs


class VoiceProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceProfile
        fields = [
            "id",
            "name",
            "status",
            "source_type",
            "provider",
            "provider_voice_id",
            "error_message",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


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
            "voice_mode",
            "voice_profile",
            "provider_voice_id",
            "fallback_voice",
            "language",
            "created_at",
            "ended_at",
            "end_reason",
            "events",
        ]
