"""HTTP request/response logging for voice agent API."""

import logging
import time
import uuid

from config.step_log import StepTimer, log_block

logger = logging.getLogger("apps.calls.http")


class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID", str(uuid.uuid4())[:8])
        request._voice_agent_request_id = request_id  # noqa: SLF001

        origin = request.META.get("HTTP_ORIGIN", "-")
        has_dev_key = bool(request.META.get("HTTP_X_VOICE_AGENT_DEV_KEY"))

        with StepTimer(
            logger,
            operation="HTTP_REQUEST",
            step=request.path,
            request_id=request_id,
            method=request.method,
            origin=origin,
            has_dev_key=has_dev_key,
        ):
            response = self.get_response(request)
            log_block(
                logger,
                logging.INFO,
                operation="HTTP_REQUEST",
                step=request.path,
                status="RESPONSE",
                request_id=request_id,
                http_status=response.status_code,
            )
            response["X-Request-Id"] = request_id
            return response
