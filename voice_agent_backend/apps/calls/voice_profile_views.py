import logging

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calls.auth import DevApiKeyAuthentication
from apps.calls.models import VoiceProfile
from apps.calls.serializers import VoiceProfileSerializer
from apps.calls.services.voice_profile_service import (
    create_cloned_voice_profile,
    create_voice_builder_json_profile,
    delete_voice_profile,
    get_voice_clone_capabilities,
    refresh_voice_profile_from_runpod,
    set_default_voice_profile,
    test_voice_profile_tts,
)
from config.step_log import StepTimer, log_block

logger = logging.getLogger("apps.calls.voice_profiles")

RECORDING_CLONE_DISABLED_MSG = (
    "Recording-based voice cloning is not enabled on this TTS server. "
    "Upload a Voice Builder JSON file instead."
)


def _rid(request) -> str:
    return getattr(request, "_voice_agent_request_id", "-")


def _consent_confirmed(request) -> bool:
    value = request.data.get("consent_confirmed")
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


class VoiceProfileListView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def get(self, request):
        profiles = VoiceProfile.objects.all()
        data = VoiceProfileSerializer(profiles, many=True).data
        caps = get_voice_clone_capabilities()
        return Response(
            {
                "voice_cloning_enabled": caps.get("voice_cloning_enabled", False),
                "supports_reference_audio_cloning": caps.get(
                    "supports_reference_audio_cloning", False
                ),
                "supports_voice_builder_json": caps.get(
                    "supports_voice_builder_json", False
                ),
                "capabilities_message": caps.get("message", ""),
                "profiles": data,
            }
        )


class CloneVoiceProfileView(APIView):
    authentication_classes = [DevApiKeyAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        request_id = _rid(request)
        if not getattr(settings, "TTS_VOICE_CLONING_ENABLED", True):
            return Response(
                {"error": "Voice cloning is disabled on this server."},
                status=status.HTTP_403_FORBIDDEN,
            )

        caps = get_voice_clone_capabilities()
        if not caps.get("supports_reference_audio_cloning"):
            return Response(
                {"error": RECORDING_CLONE_DISABLED_MSG},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if not _consent_confirmed(request):
            return Response(
                {"error": "consent_confirmed must be true"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audio = request.FILES.get("audio") or request.FILES.get("file")
        name = (request.data.get("name") or request.data.get("display_name") or "").strip()

        if not audio:
            return Response(
                {"error": "Missing audio file (field: audio or file)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not name:
            return Response(
                {"error": "Missing voice profile name."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with StepTimer(
            logger,
            "VOICE_CLONE",
            "upload_and_clone",
            request_id=request_id,
            name=name,
            filename=audio.name,
            size=audio.size,
        ):
            try:
                audio_bytes = audio.read()
                profile = create_cloned_voice_profile(
                    name=name,
                    audio_bytes=audio_bytes,
                    filename=audio.name,
                )
            except ValueError as exc:
                status_code = (
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                    if "Recording-based" in str(exc)
                    else status.HTTP_400_BAD_REQUEST
                )
                return Response({"error": str(exc)}, status=status_code)
            except Exception as exc:
                log_block(
                    logger,
                    logging.ERROR,
                    operation="VOICE_CLONE",
                    step="upload_and_clone",
                    status="FAIL",
                    request_id=request_id,
                    error=str(exc)[:400],
                )
                failed = VoiceProfile.objects.filter(
                    name=name, status=VoiceProfile.STATUS_FAILED
                ).first()
                if failed:
                    return Response(
                        {
                            "error": str(exc),
                            "profile": VoiceProfileSerializer(failed).data,
                        },
                        status=status.HTTP_502_BAD_GATEWAY,
                    )
                return Response(
                    {"error": str(exc)},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        log_block(
            logger,
            logging.INFO,
            operation="VOICE_CLONE",
            step="complete",
            status="OK" if profile.provider_voice_id else "FAIL",
            request_id=request_id,
            profile_id=str(profile.id),
            profile_status=profile.status,
            provider_voice_id=profile.provider_voice_id or "missing",
            runpod_voice_uuid=profile.runpod_voice_uuid or "none",
        )
        if profile.status != VoiceProfile.STATUS_READY or not profile.provider_voice_id:
            return Response(
                {
                    "error": profile.error_message or "Voice profile is not ready",
                    "profile": VoiceProfileSerializer(profile).data,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(VoiceProfileSerializer(profile).data, status=status.HTTP_201_CREATED)


class UploadJsonVoiceProfileView(APIView):
    authentication_classes = [DevApiKeyAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        request_id = _rid(request)
        if not getattr(settings, "TTS_VOICE_CLONING_ENABLED", True):
            return Response(
                {"error": "Voice cloning is disabled on this server."},
                status=status.HTTP_403_FORBIDDEN,
            )

        caps = get_voice_clone_capabilities()
        if not caps.get("supports_voice_builder_json"):
            return Response(
                {
                    "error": "Voice Builder JSON upload is not supported on this TTS server."
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if not _consent_confirmed(request):
            return Response(
                {"error": "consent_confirmed must be true"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        upload = request.FILES.get("file")
        name = (request.data.get("name") or request.data.get("display_name") or "").strip()

        if not upload:
            return Response(
                {"error": "Missing JSON file (field: file)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not name:
            return Response(
                {"error": "Missing voice profile display name."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with StepTimer(
            logger,
            "VOICE_JSON_UPLOAD",
            "upload_json",
            request_id=request_id,
            name=name,
            filename=upload.name,
            size=upload.size,
        ):
            try:
                profile = create_voice_builder_json_profile(
                    name=name,
                    json_bytes=upload.read(),
                    filename=upload.name,
                    consent_confirmed=True,
                )
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as exc:
                log_block(
                    logger,
                    logging.ERROR,
                    operation="VOICE_JSON_UPLOAD",
                    step="upload_json",
                    status="FAIL",
                    request_id=request_id,
                    error=str(exc)[:400],
                )
                failed = VoiceProfile.objects.filter(
                    name=name,
                    source_type=VoiceProfile.SOURCE_VOICE_BUILDER_JSON,
                    status=VoiceProfile.STATUS_FAILED,
                ).first()
                if failed:
                    return Response(
                        {
                            "error": str(exc),
                            "profile": VoiceProfileSerializer(failed).data,
                        },
                        status=status.HTTP_502_BAD_GATEWAY,
                    )
                return Response(
                    {"error": str(exc)},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        log_block(
            logger,
            logging.INFO,
            operation="VOICE_JSON_UPLOAD",
            step="complete",
            status="OK",
            request_id=request_id,
            profile_id=str(profile.id),
            provider_voice_id=profile.provider_voice_id,
        )
        return Response(VoiceProfileSerializer(profile).data, status=status.HTTP_201_CREATED)


class TestVoiceProfileView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def post(self, request, profile_id):
        profile = get_object_or_404(VoiceProfile, pk=profile_id)
        with StepTimer(
            logger,
            "VOICE_PROFILE",
            "test_tts",
            profile_id=str(profile.id),
            provider_voice_id=profile.provider_voice_id or "missing",
        ):
            try:
                audio = test_voice_profile_tts(profile)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as exc:
                profile.refresh_from_db()
                return Response(
                    {
                        "error": str(exc),
                        "profile": VoiceProfileSerializer(profile).data,
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        return HttpResponse(audio, content_type="audio/wav")


class RefreshVoiceProfileView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def post(self, request, profile_id):
        profile = get_object_or_404(VoiceProfile, pk=profile_id)
        wait = request.query_params.get("wait", "").lower() in ("1", "true", "yes")
        with StepTimer(
            logger,
            "VOICE_PROFILE",
            "refresh_from_runpod",
            profile_id=str(profile.id),
            wait=wait,
        ):
            try:
                profile = refresh_voice_profile_from_runpod(profile, wait=wait)
            except Exception as exc:
                return Response(
                    {
                        "error": str(exc),
                        "profile": VoiceProfileSerializer(profile).data,
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )
        return Response(VoiceProfileSerializer(profile).data)


class VoiceProfileDetailView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def delete(self, request, profile_id):
        profile = get_object_or_404(VoiceProfile, pk=profile_id)
        with StepTimer(
            logger,
            "VOICE_PROFILE",
            "delete",
            profile_id=str(profile.id),
            name=profile.name,
        ):
            delete_voice_profile(profile)
        return Response({"status": "deleted", "id": str(profile_id)})


class SetDefaultVoiceProfileView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def post(self, request, profile_id):
        profile = get_object_or_404(VoiceProfile, pk=profile_id)
        if profile.status != VoiceProfile.STATUS_READY:
            return Response(
                {"error": "Only ready voice profiles can be set as default."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = set_default_voice_profile(profile)
        return Response(VoiceProfileSerializer(profile).data)
