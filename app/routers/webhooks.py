from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from app.database import get_db  # Adjust to your session dependency
from app.models import EmailLog

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])

@router.post("/resend")
async def resend_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    
    event_type = payload.get("type")
    data = payload.get("data", {})
    resend_email_id = data.get("email_id")

    if not resend_email_id:
        return {"status": "ignored", "reason": "No email_id in payload"}

    # 1. Find the matching email log
    stmt = select(EmailLog).where(EmailLog.message_id == resend_email_id)
    result = await db.execute(stmt)
    email_log = result.scalar_one_or_none()

    if not email_log:
        print(f"Received webhook for untracked message_id: {resend_email_id}")
        return {"status": "not_found"}

    # 2. Handle OPEN events
    if event_type == "email.opened":
        if not email_log.opened_at:
            email_log.opened_at = datetime.utcnow()
        email_log.open_count = (email_log.open_count or 0) + 1
        await db.commit()
        print(f"🔥 EMAIL OPENED by {email_log.user_email} for listing {email_log.listing_id}")

    # 3. Handle CLICK events
    elif event_type == "email.clicked":
        click_info = data.get("click", {})
        clicked_url = click_info.get("link")

        if not email_log.clicked_at:
            email_log.clicked_at = datetime.utcnow()
        email_log.click_count = (email_log.click_count or 0) + 1
        email_log.last_clicked_url = clicked_url
        await db.commit()
        print(f"🚀 LINK CLICKED by {email_log.user_email}: {clicked_url}")

    return {"status": "success"}