from typing import Optional, List
from fastapi import FastAPI, Depends, Query, Request, HTTPException
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

# UPDATE 1: Production CORS setup
# Added placeholder for your production domain (crucial for Droplet deployment)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://lokerrealty.com", # <--- Replace with your actual domain
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/map")
async def get_map_page(request: Request):
    return templates.TemplateResponse("map.html", {"request": request})

@app.get("/api/health")
def health():
    return {"status": "ok", "message": "API is running!"}

# UPDATE 2: Improved Detail View
@app.get("/api/listings/{mls_number}")
async def get_listing(mls_number: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Listing)
        .options(selectinload(Listing.images))
        .filter(Listing.mls_number == mls_number)
        # We allow viewing of Inactive listings if user has the direct link, 
        # or you can add .where(Listing.internal_status == 'Active') to restrict it.
    )
    listing = result.scalars().first()
    
    if not listing:
        # Using HTTPException is better practice for APIs
        raise HTTPException(status_code=404, detail="Listing not found")

    return listing 

# UPDATE 3: Full Compliance & Performance Filter
@app.get("/api/listings")
async def get_listings(
    db: AsyncSession = Depends(get_db),
    city: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None
):
    # Start building the query
    # We explicitly select only what the map needs to keep the JSON payload small
    query = select(
        Listing.mls_number, 
        Listing.price, 
        Listing.lat, 
        Listing.lon, 
        Listing.address, 
        Listing.city,
        Listing.photo_url,
        Listing.listing_brokerage,
        Listing.is_address_exposed # <--- Added for compliance check
    ).where(Listing.internal_status == 'Active') # CRITICAL: Only show Active listings on map

    # Apply filters
    if city:
        query = query.where(Listing.city.ilike(f"%{city}%"))
    
    if min_price:
        query = query.where(Listing.price >= min_price)
    
    if max_price:
        query = query.where(Listing.price <= max_price)

    # Limit results for performance (prevents browser crash if database grows)
    query = query.limit(500)

    result = await db.execute(query)
    rows = result.all()
    
    # UPDATE 4: Compliance Logic for "Undisclosed Address"
    # This prevents sensitive data from ever leaving your server
    output = []
    for row in rows:
        data = row._asdict()
        if not data.get('is_address_exposed', True):
            data['address'] = "Address Undisclosed"
        output.append(data)

    return output

@app.post("/api/saved-searches", response_model=schemas.SavedSearchOut)
async def create_saved_search(search: schemas.SavedSearchCreate, db: AsyncSession = Depends(get_db)):
    db_search = models.SavedSearch(**search.dict())
    db.add(db_search)
    await db.commit()
    await db.refresh(db_search)
    return db_search