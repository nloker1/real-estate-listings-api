from typing import Optional, List
from fastapi import FastAPI, Depends, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from . import models, schemas
from app.database import get_db
from app.models import Listing

app = FastAPI(
    title="Gorge Property Search API",
    description="Real estate listings with map search",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", # Local React Dev Server
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"], # Allows GET, POST, OPTIONS, etc.
    allow_headers=["*"], # Allows all headers
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/map")
async def get_map_page(request: Request):
    return templates.TemplateResponse("map.html", {"request": request})

@app.get("/api/health")
def health():
    return {"status": "ok", "message": "API is running!"}

@app.get("/api/listings/{mls_number}")
async def get_listing(mls_number: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Listing)
        .options(selectinload(Listing.images))
        .filter(Listing.mls_number == mls_number)
    )
    listing = result.scalars().first()
    
    if not listing:
        return {"error": "Listing not found"}

    return listing # FastAPI will automatically turn this into JSON

# CLEANED UP LISTINGS ENDPOINT
@app.get("/api/listings")
async def get_listings(
    db: AsyncSession = Depends(get_db),
    city: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None
):
    # 1. Start building the query with only the columns needed for the map
    query = select(
        Listing.mls_number, 
        Listing.price, 
        Listing.lat, 
        Listing.lon, 
        Listing.address, 
        Listing.city,
        Listing.photo_url,
        Listing.listing_brokerage
    )

    # 2. Apply filters (Now these will actually work!)
    if city:
        query = query.where(Listing.city.ilike(f"%{city}%"))
    
    if min_price:
        query = query.where(Listing.price >= min_price)
    
    if max_price:
        query = query.where(Listing.price <= max_price)

    # 3. Limit results for performance
    query = query.limit(500)

    result = await db.execute(query)
    
    # 4. Format for JSON
    return [row._asdict() for row in result.all()]

# UPDATED TO ASYNC
@app.post("/api/saved-searches", response_model=schemas.SavedSearchOut)
async def create_saved_search(search: schemas.SavedSearchCreate, db: AsyncSession = Depends(get_db)):
    db_search = models.SavedSearch(**search.dict())
    db.add(db_search)
    await db.commit()
    await db.refresh(db_search)
    return db_search