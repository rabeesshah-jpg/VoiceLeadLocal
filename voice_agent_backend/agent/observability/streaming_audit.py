"""Grep-friendly audit lines for streaming / duplicate-request debugging."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent.observability.streaming_audit")


def log_streaming_audit(event: str, *, turn_id: str = "", room: str = "", **fields: Any) -> None:
    parts = [f"STREAMING_AUDIT event={event}"]
    if turn_id:
        parts.append(f"turn_id={turn_id}")
    if room:
        parts.append(f"room={room}")
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info("%s", " ".join(parts))
