import logging
import os

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.exc import SQLAlchemyError

from app.services.email_events import record_event, verify_event

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])
logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 256 * 1024


async def webhook_db():
    # Tests override this dependency without importing the normal .env loader.
    from app.database import get_db
    async for session in get_db():
        yield session


@router.post("/resend")
async def resend_webhook(request: Request, db=Depends(webhook_db)):
    secret = os.getenv("RESEND_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook verification is not configured")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook body too large")
        chunks.append(chunk)
    try:
        event = verify_event(b"".join(chunks), request.headers, secret)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid webhook") from None
    if event is None:
        return {"status": "ignored"}
    try:
        result = await record_event(db, event)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        # Never log raw payloads, client data, secrets, or SQL exception details.
        logger.error("Webhook persistence failed; provider should retry")
        raise HTTPException(status_code=503, detail="Webhook storage unavailable") from None
    return result
