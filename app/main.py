# Standard Library
from typing import List, Optional
from datetime import datetime, timedelta

# FastAPI
from fastapi import FastAPI, Depends, Query, Request, HTTPException, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

# SQLAlchemy
from sqlalchemy import select, or_, func, desc, union_all, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, Session

# Local App Imports
from . import models, schemas
from app.database import get_db
from app.models import Listing

router = APIRouter()

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
        "https://www.lokerrealty.com",
        "https://gorgerealty.com",      # <--- New
        "https://www.gorgerealty.com"   # <--- New
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ZIP_MAP = {
    "hood-river": "97031",
    "white-salmon": "98672",
    "the-dalles": "97058"
}

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
    # 1. SMART SEARCH (City OR Zip)
    search: Optional[str] = None,
    cities: Optional[List[str]] = Query(None), # <--- NEW: Multiple cities
    
    # 2. STATUS FILTER (Dynamic!)
    # Defaults to ["Active"] if the user sends nothing.
    # The user can send ?status=Active&status=Pending&status=Sold
    status: List[str] = Query(["Active"]), 
    
    # 3. NUMERIC FILTERS
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_beds: Optional[int] = None,
    min_baths: Optional[float] = None,
    min_sqft: Optional[int] = None,
    max_sqft: Optional[int] = None,
    property_type: Optional[str] = None
):
    # Start building the query
    # We ADDED: status, beds, baths, sqft, zipcode so the map popup can show them
    query = select(
        Listing.mls_number, 
        Listing.price, 
        Listing.status,         # <--- Added
        Listing.lat, 
        Listing.lon, 
        Listing.address, 
        Listing.city,
        Listing.zipcode,        # <--- Added
        Listing.beds,           # <--- Added
        Listing.baths,          # <--- Added
        Listing.sqft,           # <--- Added
        Listing.photo_url,
        Listing.listing_brokerage,
        Listing.is_address_exposed
    ).where(Listing.is_published == True) # Ensure we don't show internal/hidden data

    # --- APPLY FILTERS ---

    # 1. Status Logic
    if status:
        query = query.where(Listing.status.in_(status))

    # 2. Multi-City Logic (Takes priority over search if both provided)
    if cities:
        # Standardize search by removing spaces to match DB (e.g., "Hood River" -> "HoodRiver")
        normalized_cities = [c.replace(" ", "") for c in cities]
        city_filters = [Listing.city.ilike(f"%{c}%") for c in normalized_cities]
        query = query.where(or_(*city_filters))
    elif search:
        # Also normalize the single search term
        normalized_search = search.replace(" ", "")
        search_term = f"%{normalized_search}%"
        query = query.where(
            or_(
                Listing.city.ilike(search_term),
                Listing.zipcode.ilike(search_term)
            )
        )
    
    # 3. Numeric Filters
    if min_price:
        query = query.where(Listing.price >= min_price)
    if max_price:
        query = query.where(Listing.price <= max_price)
    if min_beds:
        query = query.where(Listing.beds >= min_beds)
    if min_baths:
        query = query.where(Listing.baths >= min_baths)
    if min_sqft:
        query = query.where(Listing.sqft >= min_sqft)
    if max_sqft:
        query = query.where(Listing.sqft <= max_sqft)
    if property_type:
        query = query.where(Listing.property_type == property_type)

    # Limit results (Sold data can be huge, so 500 is a safe limit for now)
    query = query.limit(500)

    result = await db.execute(query)
    rows = result.all()
    
    # FIX: Convert the SQLAlchemy "Rows" into standard Python Dictionaries
    return [dict(row._mapping) for row in rows]
    
    # UPDATE 4: Compliance Logic for "Undisclosed Address"
    # This prevents sensitive data from ever leaving your server
    output = []
    for row in rows:
        data = row._asdict()
        if not data.get('is_address_exposed', True):
            data['address'] = "Address Undisclosed"
        output.append(data)

    return output

@app.get("/api/market/{city_slug}")
async def get_market_hub_data(city_slug: str, db: AsyncSession = Depends(get_db)):
    
    # 1. VALIDATE CITY
    target_zip = ZIP_MAP.get(city_slug)
    if not target_zip:
        raise HTTPException(status_code=404, detail="Market not found for this city.")
    
    one_year_ago = datetime.now() - timedelta(days=365)

    # =========================================================
    # QUERY 1: Active Market Stats
    # =========================================================
    stats_stmt = select(
        func.percentile_cont(0.5).within_group(Listing.price).label("median_price"),
        func.percentile_cont(0.5).within_group(Listing.days_on_market).label("median_dom"),
        func.count(Listing.id).label("active_count")
    ).where(
        Listing.zipcode == target_zip, 
        Listing.status == 'Active',
        Listing.property_type != 'Land'
    )

    stats_result = await db.execute(stats_stmt)
    stats = stats_result.first()

    # =========================================================
    # QUERY 2: Top 5 Realtors
    # =========================================================
    q_list = select(
        Listing.list_agent_name.label("agent_name"), 
        Listing.id.label("listing_id"),
        Listing.price
    ).where(
        Listing.zipcode == target_zip,
        Listing.status == 'Sold',
        Listing.close_date >= one_year_ago,
        Listing.list_agent_name.isnot(None)
    )

    q_buyer = select(
        Listing.buyer_agent_name.label("agent_name"), 
        Listing.id.label("listing_id"),
        Listing.price
    ).where(
        Listing.zipcode == target_zip,
        Listing.status == 'Sold',
        Listing.close_date >= one_year_ago,
        Listing.buyer_agent_name.isnot(None)
    )

    agent_sides = union_all(q_list, q_buyer).cte("agent_sides")

    realtor_stmt = select(
        agent_sides.c.agent_name,
        func.sum(agent_sides.c.price).label("total_volume"),
        func.count(agent_sides.c.listing_id).label("transactions")
    ).group_by(
        agent_sides.c.agent_name
    ).order_by(
        desc("total_volume")
    ).limit(5)

    realtor_result = await db.execute(realtor_stmt)
    top_realtors = realtor_result.all()

    # =========================================================
    # QUERY 3: 12-Month Trend (RAW SQL VERSION)
    # =========================================================
    # Note: We import text inside the function just to be safe, 
    # but normally it goes at the top of the file
    
    # TODO (Refactor): Convert this to pure SQLAlchemy CTE syntax once the 
    # date_trunc grouping issue is resolved. For now, raw SQL is stable.
    raw_sql = text("""
        SELECT 
            DATE_TRUNC('month', close_date) AS month_date, 
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price) AS median_price,
            COUNT(*) AS sales_count
        FROM listings
        WHERE 
            zipcode = :zipcode 
            AND status = 'Sold' 
            AND close_date >= :one_year_ago
            AND property_type != 'Land'
        GROUP BY 
            DATE_TRUNC('month', close_date)
        ORDER BY 
            month_date;
    """)

    trend_result = await db.execute(raw_sql, {
        "zipcode": target_zip, 
        "one_year_ago": one_year_ago
    })
    
    trends = trend_result.all()

    # =========================================================
    # FORMATTING
    # =========================================================
    median_price = int(stats.median_price) if stats and stats.median_price else 0
    days_on_market = int(stats.median_dom) if stats and stats.median_dom else 0
    active_count = stats.active_count if stats else 0

    formatted_realtors = []
    for index, r in enumerate(top_realtors):
        vol = r.total_volume or 0
        vol_in_millions = vol / 1000000
        formatted_realtors.append({
            "id": index + 1,
            "name": r.agent_name,
            "volume": f"${vol_in_millions:.1f}M",
            "transactions": r.transactions
        })

    formatted_trends = []
    for t in trends:
        # Safety check: t.month_date might be None if data is messy
        if t.month_date:
            month_str = t.month_date.strftime("%b")
        else:
            month_str = "Unk"
            
        formatted_trends.append({
            "month": month_str,
            "price": int(t.median_price) if t.median_price else 0,
            "count": t.sales_count
        })

    return {
        "marketData": {
            "medianPrice": median_price,
            "daysOnMarket": days_on_market,
            "activeListings": active_count
        },
        "topRealtors": formatted_realtors,
        "trendData": formatted_trends
    }


@app.post("/api/saved-searches", response_model=schemas.SavedSearchCreate)
async def create_saved_search(search: schemas.SavedSearchCreate, db: AsyncSession = Depends(get_db)):
    db_search = models.SavedSearch(**search.dict())
    db.add(db_search)
    await db.commit()
    await db.refresh(db_search)
    return db_search