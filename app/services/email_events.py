"""Verified Resend event ingestion and transactional, retry-safe reporting updates."""
import json
from datetime import datetime, timezone

from resend import Webhooks
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from app.models import EmailEvent, EmailLog

EVENT_TYPES = {
    "email.sent", "email.scheduled", "email.delivered", "email.delivery_delayed",
    "email.opened", "email.clicked", "email.bounced", "email.complained",
    "email.failed", "email.suppressed",
}


def verify_event(body, headers, secret):
    """Verify original bytes, then normalize; never store recipients or subjects."""
    payload = body.decode("utf-8")
    Webhooks.verify({
        "payload": payload,
        "headers": {"id": headers.get("svix-id"),
                    "timestamp": headers.get("svix-timestamp"),
                    "signature": headers.get("svix-signature")},
        "webhook_secret": secret,
    })
    event = json.loads(payload)
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("Invalid event")
    event_type = event["type"]
    if event_type not in EVENT_TYPES:
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        raise ValueError("Invalid event data")
    event_id, message_id = headers.get("svix-id"), data.get("email_id")
    if any(not isinstance(value, str) or not value or len(value) > 255
           for value in (event_id, message_id)):
        raise ValueError("Invalid event identifier")
    occurred = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
    if occurred.tzinfo is None:
        raise ValueError("Event timestamp must include a timezone")
    details = {}
    if event_type in {"email.opened", "email.clicked"}:
        details["engagement_source"] = "unclassified"
    if event_type == "email.clicked":
        click = data.get("click", {})
        link = click.get("link") if isinstance(click, dict) else None
        if not isinstance(link, str) or not link or len(link) > 8192:
            raise ValueError("Invalid clicked URL")
        details["link"] = link
    if event_type == "email.bounced":
        bounce = data.get("bounce", {})
        if isinstance(bounce, dict):
            for key in ("type", "subType"):
                if isinstance(bounce.get(key), str):
                    details[key] = bounce[key][:100]
    return {"provider_event_id": event_id, "message_id": message_id,
            "event_type": event_type, "occurred_at": occurred.astimezone(timezone.utc),
            "details": details, "processing_status": "pending"}


async def reconcile_message(db, message_id):
    """Serialize counter updates with a row lock. Caller owns commit/rollback."""
    logs = (await db.execute(select(EmailLog).where(
        EmailLog.message_id == message_id).order_by(EmailLog.id).limit(2).with_for_update()
    )).scalars().all()
    # Unknown or ambiguous associations must not be attributed to a client.
    if len(logs) != 1:
        return 0
    log = logs[0]
    events = (await db.execute(select(EmailEvent).where(
        EmailEvent.message_id == message_id, EmailEvent.processing_status == "pending",
    ).order_by(EmailEvent.occurred_at, EmailEvent.provider_event_id).with_for_update())).scalars().all()
    for event in events:
        if event.event_type == "email.opened":
            log.open_count = (log.open_count or 0) + 1
            log.opened_at = min(log.opened_at, event.occurred_at) if log.opened_at else event.occurred_at
        elif event.event_type == "email.clicked":
            log.click_count = (log.click_count or 0) + 1
            log.clicked_at = min(log.clicked_at, event.occurred_at) if log.clicked_at else event.occurred_at
        event.email_log_id = log.id
        event.processing_status = "processed"
    await db.flush()
    if any(event.event_type == "email.clicked" for event in events):
        latest = (await db.execute(select(EmailEvent).where(
            EmailEvent.email_log_id == log.id, EmailEvent.event_type == "email.clicked",
            EmailEvent.processing_status == "processed",
        ).order_by(EmailEvent.occurred_at.desc(), EmailEvent.provider_event_id.desc()).limit(1))).scalar_one()
        log.last_clicked_url = latest.details["link"]
    return len(events)


async def record_event(db, values):
    inserted = (await db.execute(insert(EmailEvent).values(**values).on_conflict_do_nothing(
        index_elements=[EmailEvent.provider_event_id]
    ).returning(EmailEvent.id))).scalar_one_or_none()
    # Reconcile duplicate receipts too, but never trust changed duplicate content.
    message_id = (await db.execute(select(EmailEvent.message_id).where(
        EmailEvent.provider_event_id == values["provider_event_id"]))).scalar_one()
    applied = await reconcile_message(db, message_id)
    return {"status": "recorded" if inserted is not None else "duplicate", "applied": applied}


async def reconcile_pending(db, limit=100):
    message_ids = (await db.execute(select(EmailEvent.message_id).where(
        EmailEvent.processing_status == "pending",
        # Do not let ambiguous associations consume every batch slot forever.
        EmailEvent.message_id.in_(select(EmailLog.message_id).group_by(
            EmailLog.message_id).having(func.count(EmailLog.id) == 1)),
    ).distinct().order_by(EmailEvent.message_id).limit(limit))).scalars().all()
    return sum([await reconcile_message(db, message_id) for message_id in message_ids])
