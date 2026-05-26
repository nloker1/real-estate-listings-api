from fastapi import APIRouter, Depends
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import SavedSearch, EmailLog

router = APIRouter(prefix="/debug")

@router.get("/searches")
async def get_searches(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SavedSearch).where(SavedSearch.id.in_([3, 11, 12])))
    return [{"id": s.id, "criteria": s.criteria} for s in res.scalars()]

@router.get("/logs")
async def get_logs(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(EmailLog).order_by(EmailLog.id.desc()).limit(10))
    return [{"id": l.id, "search_id": l.search_id, "listing_id": l.listing_id} for l in res.scalars()]
