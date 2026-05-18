import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calls.auth import DevApiKeyAuthentication
from apps.calls.models import CallSession
from apps.calls.serializers import (
    CallSessionDetailSerializer,
    EndCallSerializer,
    StartCallSerializer,
)
from apps.calls.services import LiveKitTokenService, SessionRegistry
from apps.calls.services.livekit_room import ensure_room_and_dispatch_agent
from config.step_log import StepTimer, log_block

logger = logging.getLogger("apps.calls.views")


def _rid(request) -> str:
    return getattr(request, "_voice_agent_request_id", "-")


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        request_id = _rid(request)
        with StepTimer(logger, "HEALTH_CHECK", "full", request_id=request_id):
            lk_missing = LiveKitTokenService().validate_config()
            provider_missing = SessionRegistry.validate_providers()
            payload = {
                "status": "ok" if not lk_missing and not provider_missing else "degraded",
                "livekit_configured": len(lk_missing) == 0,
                "providers_configured": len(provider_missing) == 0,
                "missing": lk_missing + provider_missing,
                "livekit_url_set": bool(settings.LIVEKIT_URL),
            }
            log_block(
                logger,
                logging.INFO,
                operation="HEALTH_CHECK",
                step="result",
                status="OK",
                request_id=request_id,
                result=payload["status"],
                missing=payload["missing"] or "none",
            )
            return Response(payload)


class StartCallView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def post(self, request):
        request_id = _rid(request)
        operation = "START_CALL"

        with StepTimer(
            logger,
            operation,
            "validate_request",
            request_id=request_id,
            origin=request.META.get("HTTP_ORIGIN", "-"),
        ):
            serializer = StartCallSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

        persona_id = serializer.validated_data.get("persona_id", "")
        system_prompt = serializer.validated_data.get("system_prompt", "")

        lk_service = LiveKitTokenService()
        with StepTimer(logger, operation, "livekit_config", request_id=request_id):
            lk_missing = lk_service.validate_config()
            if lk_missing:
                log_block(
                    logger,
                    logging.ERROR,
                    operation=operation,
                    step="livekit_config",
                    status="FAIL",
                    request_id=request_id,
                    missing=lk_missing,
                )
                return Response(
                    {"error": "LiveKit is not configured.", "missing": lk_missing},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        with StepTimer(logger, operation, "provider_config", request_id=request_id):
            provider_missing = SessionRegistry.validate_providers()
            if provider_missing:
                log_block(
                    logger,
                    logging.ERROR,
                    operation=operation,
                    step="provider_config",
                    status="FAIL",
                    request_id=request_id,
                    missing=provider_missing,
                )
                return Response(
                    {"error": "Voice providers are not configured.", "missing": provider_missing},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        with StepTimer(
            logger,
            operation,
            "create_session",
            request_id=request_id,
            persona_id=persona_id or "default",
        ):
            session = SessionRegistry.create_session(
                persona_id=persona_id,
                system_prompt=system_prompt,
            )

        if not getattr(settings, "VOICE_AGENT_SKIP_ROOM_SETUP", False):
            try:
                with StepTimer(
                    logger,
                    operation,
                    "dispatch_agent_worker",
                    request_id=request_id,
                    call_id=str(session.id),
                    room=session.room_name,
                ):
                    dispatch_info = ensure_room_and_dispatch_agent(
                        room_name=session.room_name,
                        call_id=str(session.id),
                        system_prompt=session.system_prompt,
                    )
                    log_block(
                        logger,
                        logging.INFO,
                        operation=operation,
                        step="dispatch_agent_worker",
                        status="DISPATCHED",
                        request_id=request_id,
                        dispatch_id=dispatch_info.get("dispatch_id"),
                    )
            except Exception as exc:
                logger.exception("Agent dispatch failed")
                session.status = CallSession.STATUS_FAILED
                session.save(update_fields=["status", "updated_at"])
                return Response(
                    {
                        "error": "Failed to dispatch voice agent to room.",
                        "detail": str(exc),
                        "hint": "Ensure python -m agent dev is running and registered as voice-agent",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        else:
            log_block(
                logger,
                logging.WARNING,
                operation=operation,
                step="dispatch_agent_worker",
                status="SKIP",
                request_id=request_id,
                reason="VOICE_AGENT_SKIP_ROOM_SETUP",
            )

        try:
            with StepTimer(
                logger,
                operation,
                "mint_livekit_token",
                request_id=request_id,
                call_id=str(session.id),
                room=session.room_name,
            ):
                participant = lk_service.mint_participant_token(
                    session.room_name,
                    session.user_identity,
                )
        except Exception as exc:
            logger.exception("Token mint failed")
            session.status = CallSession.STATUS_FAILED
            session.save(update_fields=["status", "updated_at"])
            return Response(
                {"error": "Failed to mint LiveKit token.", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        with StepTimer(
            logger,
            operation,
            "activate_session",
            request_id=request_id,
            call_id=str(session.id),
        ):
            SessionRegistry.activate_session(session, participant.expires_at)
            SessionRegistry.log_event(
                session,
                "token_issued",
                {"identity": participant.identity, "request_id": request_id},
            )

        log_block(
            logger,
            logging.INFO,
            operation=operation,
            step="complete",
            status="SUCCESS",
            request_id=request_id,
            call_id=str(session.id),
            room=session.room_name,
            identity=participant.identity,
            livekit_url=participant.livekit_url,
            expires_at=participant.expires_at.isoformat(),
        )

        return Response(
            {
                "call_id": str(session.id),
                "room_name": session.room_name,
                "livekit_url": participant.livekit_url,
                "participant_token": participant.token,
                "participant_identity": participant.identity,
                "expires_at": participant.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class EndCallView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def post(self, request, call_id):
        request_id = _rid(request)
        with StepTimer(logger, "END_CALL", "full", request_id=request_id, call_id=str(call_id)):
            serializer = EndCallSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            session = get_object_or_404(CallSession, pk=call_id)
            reason = serializer.validated_data.get("reason", "user_ended")
            if session.status != CallSession.STATUS_ENDED:
                session.mark_ended(reason)
                SessionRegistry.log_event(
                    session,
                    "session_ended",
                    {"reason": reason, "request_id": request_id},
                )
            return Response({"status": "ended", "call_id": str(session.id)})


class CallDetailView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def get(self, request, call_id):
        with StepTimer(logger, "CALL_DETAIL", "fetch", request_id=_rid(request), call_id=str(call_id)):
            session = get_object_or_404(CallSession, pk=call_id)
            data = CallSessionDetailSerializer(session).data
            metrics = _aggregate_latency_metrics(session)
            if metrics:
                data["metrics"] = metrics
            return Response(data)


class CallEventsView(APIView):
    authentication_classes = [DevApiKeyAuthentication]

    def get(self, request, call_id):
        with StepTimer(logger, "CALL_EVENTS", "fetch", request_id=_rid(request), call_id=str(call_id)):
            session = get_object_or_404(CallSession, pk=call_id)
            events = session.events.order_by("created_at")
            return Response(
                {
                    "call_id": str(session.id),
                    "events": [
                        {
                            "event_type": e.event_type,
                            "payload": e.payload,
                            "created_at": e.created_at.isoformat(),
                        }
                        for e in events
                    ],
                }
            )


def _aggregate_latency_metrics(session: CallSession) -> dict:
    latency_events = session.events.filter(event_type="latency").order_by("created_at")
    if not latency_events.exists():
        return {}
    samples = [e.payload for e in latency_events]

    def avg(key):
        vals = [s.get(key) for s in samples if s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "turn_count": len(samples),
        "avg_stt_ms": avg("stt_ms"),
        "avg_llm_first_token_ms": avg("llm_first_token_ms"),
        "avg_tts_first_byte_ms": avg("tts_first_byte_ms"),
    }
