from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, EmailStr
from typing import Optional
import uuid

from app.database import get_db
from app.models import Lead, SavedSearch, Listing

router = APIRouter(
    prefix="/api/alerts",
    tags=["alerts"]
)

class SubscribeRequest(BaseModel):
    email: EmailStr
    alert_type: str  # "property" or "market"
    target_id: str   # mls_number or city_name

@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe_to_alert(req: SubscribeRequest, db: AsyncSession = Depends(get_db)):
    # Validate alert_type
    if req.alert_type not in ["property", "market"]:
        raise HTTPException(status_code=400, detail="Invalid alert_type. Must be 'property' or 'market'.")

    # 1. Find or create the Lead
    stmt = select(Lead).where(Lead.email == req.email)
    result = await db.execute(stmt)
    lead = result.scalar_one_or_none()

    if not lead:
        lead = Lead(
            email=req.email,
            unsubscribe_token=str(uuid.uuid4())
        )
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
    else:
        # If they previously unsubscribed, resubscribe them
        if lead.is_unsubscribed:
            lead.is_unsubscribed = False
            lead.unsubscribed_at = None
            if not lead.unsubscribe_token:
                lead.unsubscribe_token = str(uuid.uuid4())
            await db.commit()

    # 2. Build the criteria JSON based on alert_type
    if req.alert_type == "property":
        # Fetch current listing to seed baseline
        stmt_listing = select(Listing).where(Listing.mls_number == str(req.target_id))
        res_listing = await db.execute(stmt_listing)
        listing = res_listing.scalar_one_or_none()
        
        criteria = {
            "alert_type": "property", 
            "mls_number": req.target_id,
            "last_price": listing.price if listing else None,
            "last_status": listing.status if listing else None
        }
        frequency = "instant"
    else:
        criteria = {"alert_type": "market", "city": req.target_id}
        frequency = "weekly"

    # 3. Check if they already have this exact alert
    stmt_search = select(SavedSearch).where(SavedSearch.lead_id == lead.id)
    result_search = await db.execute(stmt_search)
    existing_searches = result_search.scalars().all()

    for search in existing_searches:
        if search.criteria.get("alert_type") == criteria["alert_type"]:
            if req.alert_type == "property" and search.criteria.get("mls_number") == criteria["mls_number"]:
                return {"message": "Already subscribed to this property"}
            if req.alert_type == "market" and search.criteria.get("city") == criteria["city"]:
                return {"message": "Already subscribed to this market"}

    # 4. Create the new SavedSearch record
    new_search = SavedSearch(
        lead_id=lead.id,
        criteria=criteria,
        frequency=frequency,
        is_active=True
    )
    db.add(new_search)
    await db.commit()

    return {"success": True, "message": "Alert created successfully"}

@router.get("/unsubscribe")
async def unsubscribe_lead(token: str, db: AsyncSession = Depends(get_db)):
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")

    stmt = select(Lead).where(Lead.unsubscribe_token == token)
    result = await db.execute(stmt)
    lead = result.scalar_one_or_none()

    if not lead:
        raise HTTPException(status_code=404, detail="Invalid unsubscribe token")

    lead.is_unsubscribed = True
    lead.unsubscribed_at = __import__('datetime').datetime.utcnow()
    
    await db.commit()
    
    return {"success": True, "message": "Successfully unsubscribed"}
