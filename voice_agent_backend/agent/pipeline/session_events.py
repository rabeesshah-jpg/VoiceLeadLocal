"""Persist worker events to Django CallEvent when ORM is available."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("agent.session_events")


def _call_id_from_room(room_name: str) -> str | None:
    # room_name: va-{12 hex chars} -> call_id is full uuid stored in DB
    m = re.match(r"^va-([0-9a-f]{12})$", room_name or "")
    if not m:
        return None
    prefix = m.group(1)
    try:
        from apps.calls.models import CallSession

        session = CallSession.objects.filter(room_name=room_name).first()
        if session:
            return str(session.id)
    except Exception as exc:
        logger.debug("ORM lookup failed: %s", exc)
    return prefix


def _persist_call_event(room_name: str, event_type: str, payload: dict | None = None) -> None:
    from apps.calls.models import CallEvent, CallSession

    session = CallSession.objects.filter(room_name=room_name).first()
    if not session:
        return
    CallEvent.objects.create(
        session=session,
        event_type=event_type,
        payload=payload or {},
    )


async def log_call_event(room_name: str, event_type: str, payload: dict | None = None) -> None:
    from asgiref.sync import sync_to_async

    try:
        await sync_to_async(_persist_call_event, thread_sensitive=True)(
            room_name, event_type, payload
        )
    except Exception as exc:
        logger.warning("Could not persist call event %s: %s", event_type, exc)
