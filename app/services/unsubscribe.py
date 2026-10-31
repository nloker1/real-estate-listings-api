"""Stable opaque unsubscribe tokens, including repair of older lead records."""
from uuid import uuid4
from sqlalchemy import select, update, or_
from app.models import Lead


async def ensure_unsubscribe_token(db, lead):
    if lead.unsubscribe_token:
        return lead.unsubscribe_token
    # Conditional update avoids rotating a token when concurrent jobs repair
    # the same client. The caller commits before including it in an email.
    await db.execute(update(Lead).where(
        Lead.id == lead.id, or_(Lead.unsubscribe_token.is_(None), Lead.unsubscribe_token == '')
    ).values(unsubscribe_token=str(uuid4())).execution_options(synchronize_session=False))
    token = (await db.execute(select(Lead.unsubscribe_token).where(Lead.id == lead.id))).scalar_one()
    await db.refresh(lead, ['unsubscribe_token'])
    return token
