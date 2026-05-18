"""Dev API key authentication."""

import logging

from django.conf import settings
from rest_framework import authentication, exceptions

from config.step_log import log_block

logger = logging.getLogger("apps.calls.auth")


class DevApiKeyAuthentication(authentication.BaseAuthentication):
    header_name = "HTTP_X_VOICE_AGENT_DEV_KEY"

    def authenticate(self, request):
        request_id = getattr(request, "_voice_agent_request_id", "-")

        if settings.DEBUG and not settings.VOICE_AGENT_DEV_API_KEY:
            log_block(
                logger,
                logging.DEBUG,
                operation="AUTH",
                step="dev_bypass",
                status="OK",
                request_id=request_id,
            )
            return (None, None)

        expected = settings.VOICE_AGENT_DEV_API_KEY
        if not expected:
            log_block(
                logger,
                logging.ERROR,
                operation="AUTH",
                step="server_config",
                status="FAIL",
                request_id=request_id,
                reason="VOICE_AGENT_DEV_API_KEY not set",
            )
            raise exceptions.AuthenticationFailed("VOICE_AGENT_DEV_API_KEY is not configured.")

        provided = request.META.get(self.header_name, "")
        source = "header"
        if not provided:
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:].strip()
                source = "bearer"

        if not provided:
            log_block(
                logger,
                logging.WARNING,
                operation="AUTH",
                step="validate_key",
                status="FAIL",
                request_id=request_id,
                path=request.path,
                reason="missing_header",
            )
            raise exceptions.AuthenticationFailed("Missing X-Voice-Agent-Dev-Key header.")

        if provided != expected:
            log_block(
                logger,
                logging.WARNING,
                operation="AUTH",
                step="validate_key",
                status="FAIL",
                request_id=request_id,
                path=request.path,
                reason="key_mismatch",
                source=source,
            )
            raise exceptions.AuthenticationFailed("Invalid voice agent API key.")

        log_block(
            logger,
            logging.DEBUG,
            operation="AUTH",
            step="validate_key",
            status="OK",
            request_id=request_id,
            path=request.path,
            source=source,
        )
        return (None, None)
