# app/routers/leads.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Lead, SavedSearch
from app.schemas import AlertRequest  # <--- IMPORT FROM SCHEMAS

router = APIRouter()

@router.post("/save-search")
async def save_search(request: AlertRequest, db: AsyncSession = Depends(get_db)):
    # 1. Validation Logic
    if not request.phone and not request.email:
        raise HTTPException(status_code=400, detail="Phone or Email required")

    # 2. Database Logic (Find or Create Lead)
    lead = None
    if request.phone:
        result = await db.execute(select(Lead).where(Lead.phone == request.phone))
        lead = result.scalars().first()
    
    # If phone didn't find one, try email
    if not lead and request.email:
        result = await db.execute(select(Lead).where(Lead.email == request.email))
        lead = result.scalars().first()
        
    # If still no lead, create one
    if not lead:
        lead = Lead(phone=request.phone, email=request.email)
        db.add(lead)
        await db.flush() # Get the ID immediately

    # 3. Save the Search
    new_search = SavedSearch(
        lead_id=lead.id,
        criteria=request.criteria,
        frequency="instant"
    )
    db.add(new_search)
    
    await db.commit()
    return {"status": "success", "message": "Alert created!", "lead_id": lead.id}