"""Post-call lead summary delivery via Twilio (SMS today, WhatsApp once a
message template is approved).

Sends the caller a short recap of what they told Noura plus the Calendly
link, right after the call ends. Never call the blocking Twilio client
directly from async code — always go through send_lead_summary(), which
wraps the network call in sync_to_async.
"""

from __future__ import annotations

import logging
import os

from asgiref.sync import sync_to_async
from twilio.rest import Client

from agent.prompts import CALENDLY_LINK

logger = logging.getLogger("agent.lead_summary")

# Fields on the Lead model to include, in display order — English and
# Arabic labels kept in the same order so the two versions line up.
_FIELD_LABELS_EN: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("company", "Company"),
    ("city", "City"),
    ("need", "Needs"),
    ("business_description", "Business"),
    ("start_timeline", "Timeline"),
)

_FIELD_LABELS_AR: tuple[tuple[str, str], ...] = (
    ("name", "الاسم"),
    ("company", "الشركة"),
    ("city", "المدينة"),
    ("need", "الاحتياج"),
    ("business_description", "النشاط التجاري"),
    ("start_timeline", "الجدول الزمني"),
)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def build_summary_message(lead, *, language: str = "en") -> str:
    """Build the summary text from a Lead model instance, in whichever
    language the call was actually conducted in (tracked on
    CallSession.language, including any mid-call DTMF switch to Arabic).
    """
    is_arabic = (language or "en").strip().lower() == "ar"
    field_labels = _FIELD_LABELS_AR if is_arabic else _FIELD_LABELS_EN

    if is_arabic:
        greeting_name = lead.name or "عميلنا"
        lines = [
            f"مرحباً {greeting_name}، شكراً لتواصلك مع Good Websites! إليك ملخص سريع:",
            "",
        ]
    else:
        greeting_name = lead.name or "there"
        lines = [
            f"Hi {greeting_name}, thanks for calling Good Websites! Here's a quick summary:",
            "",
        ]

    for field, label in field_labels:
        value = getattr(lead, field, None)
        if value:
            lines.append(f"{label}: {value}")

    lines.append("")
    if is_arabic:
        lines.append(f"احجز الوقت المناسب لك: {CALENDLY_LINK}")
    else:
        lines.append(f"Book a time that works for you: {CALENDLY_LINK}")

    return "\n".join(lines)


def _twilio_client() -> Client:
    account_sid = _env("TWILIO_ACCOUNT_SID")
    auth_token = _env("TWILIO_AUTH_TOKEN")
    if not (account_sid and auth_token):
        raise RuntimeError(
            "Twilio not configured: set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN."
        )
    return Client(account_sid, auth_token)


def _send_sms(to_number: str, body: str) -> str:
    from_number = _env("TWILIO_SMS_FROM")  # your Twilio SMS-capable number, e.g. "+19106599230"
    if not from_number:
        raise RuntimeError("TWILIO_SMS_FROM is not set.")
    client = _twilio_client()
    message = client.messages.create(from_=from_number, to=to_number, body=body)
    return message.sid


def _send_whatsapp(to_number: str, body: str) -> str:
    """Freeform WhatsApp send. Only works within Twilio/Meta's 24h session
    window (i.e. the caller messaged your WhatsApp number recently) — a
    phone call does NOT open that window. For business-initiated sends like
    this one, use _send_whatsapp_template() instead once a template is
    approved.
    """
    from_number = _env("TWILIO_WHATSAPP_FROM")  # e.g. "whatsapp:+14155238886"
    if not from_number:
        raise RuntimeError("TWILIO_WHATSAPP_FROM is not set.")
    client = _twilio_client()
    message = client.messages.create(
        from_=from_number, to=f"whatsapp:{to_number}", body=body
    )
    return message.sid


def _send_whatsapp_template(to_number: str, content_sid: str, content_variables: dict) -> str:
    """Business-initiated WhatsApp send using an approved Content Template.
    content_sid comes from Twilio Content API / Console once your template
    is approved. content_variables maps the template's numbered
    placeholders (e.g. {"1": name, "2": summary_body}) to values.
    """
    from_number = _env("TWILIO_WHATSAPP_FROM")
    if not from_number:
        raise RuntimeError("TWILIO_WHATSAPP_FROM is not set.")
    client = _twilio_client()
    message = client.messages.create(
        from_=from_number,
        to=f"whatsapp:{to_number}",
        content_sid=content_sid,
        content_variables=str(content_variables).replace("'", '"'),
    )
    return message.sid


@sync_to_async
def send_lead_summary(to_number: str, lead, *, channel: str = "sms", language: str = "en") -> str:
    """Send the lead summary, in whichever language the call was actually
    conducted in. Returns the Twilio message SID.

    channel: "sms" (default, works immediately, no approval needed) or
    "whatsapp" (requires an approved template for business-initiated
    sends — see _send_whatsapp_template above).

    Call with `await` from async code. Raises on failure — callers should
    catch and log rather than letting this break call-end handling.
    """
    body = build_summary_message(lead, language=language)
    if channel == "sms":
        sid = _send_sms(to_number, body)
    elif channel == "whatsapp":
        sid = _send_whatsapp(to_number, body)
    else:
        raise ValueError(f"Unknown channel: {channel!r}")
    logger.info(
        "lead_summary sent channel=%s language=%s to=%s sid=%s chars=%s",
        channel, language, to_number, sid, len(body),
    )
    return sid