import sys
import os
import httpx
import asyncio
import time
from dotenv import load_dotenv
from datetime import datetime
from dateutil import parser

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Listing
from app.database import AsyncSessionLocal
from sqlalchemy import select, update
from sqlalchemy.orm import configure_mappers

configure_mappers()

# --- CONFIGURATION ---
START_YEAR = 2015
END_YEAR = 2025 
GORGE_ZIPS = [
    "97031", "97041", "97044", "97040", "97014", "97058", "97021", 
    "97028", "98672", "98605", "98651", "98635", "98650", "98648", "98617"
]

def safe_float(value):
    try:
        return float(value) if value is not None else None
    except:
        return None

def safe_int(value):
    try:
        return int(float(value)) if value is not None else None
    except:
        return None

async def fetch_year(year, client, headers, base_url):
    print(f"\n📅 STARTING YEAR: {year}")
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"
    zip_filter = " or ".join([f"PostalCode eq '{z}'" for z in GORGE_ZIPS])
    
    status_filter = (
        f"StandardStatus eq Odata.Models.StandardStatus'Closed' and "
        f"CloseDate ge {start_date} and CloseDate le {end_date}"
    )

    final_filter = f"({zip_filter}) and ({status_filter})"
    
    # Selecting the new fields
    params = {
        "$filter": final_filter,
        "$select": "ListingId,ListPrice,ClosePrice,CloseDate,City,UnparsedAddress,BedroomsTotal,BathsTotal,Photo1URL,Latitude,Longitude,YearBuilt,ListAgentFullName,BuyerAgentFullName,PublicRemarks,PropertyType,StandardStatus,MlsStatus,PostalCode,ListOfficeName,DaysOnMarket,CumulativeDaysOnMarket",
        "$top": 250
    }

    url_to_fetch = base_url
    params_to_use = params
    listings_batch = []
    page = 1

    while url_to_fetch:
        try:
            await asyncio.sleep(0.5) 
            response = await client.get(url_to_fetch, headers=headers, params=params_to_use)
            if response.status_code != 200:
                print(f"   ❌ Error on Year {year} Page {page}: {response.status_code}")
                return []

            data = response.json()
            items = data.get('value', [])
            if not items: break

            listings_batch.extend(items)
            print(f"   ⬇️ Year {year} | Page {page} | Found {len(items)} items...")

            next_link = data.get('@odata.nextLink')
            if next_link:
                url_to_fetch = next_link
                params_to_use = None
                page += 1
            else:
                url_to_fetch = None
        except Exception as e:
            print(f"   ❌ Crash on year {year}: {e}")
            return []
            
    return listings_batch

async def save_batch(listings, db):
    count_new = 0
    count_updated = 0
    
    for item in listings:
        mls_id = str(item.get('ListingId'))
        
        # 1. Check if exists
        result = await db.execute(select(Listing).filter(Listing.mls_number == mls_id))
        existing = result.scalars().first()
        
        # Prepare Data
        rmls_dom = safe_int(item.get('DaysOnMarket'))
        rmls_cdom = safe_int(item.get('CumulativeDaysOnMarket'))
        
        # 2. THE UPSERT LOGIC
        if existing:
            # Check if we actually need to update (optimization)
            if existing.rmls_dom != rmls_dom or existing.rmls_cdom != rmls_cdom:
                existing.rmls_dom = rmls_dom
                existing.rmls_cum_dom = rmls_cdom
                # Update other fields if you suspect they changed, or leave them
                count_updated += 1
        else:
            # Create New
            listing_data = {
                "mls_number": mls_id,
                "status": 'Sold',
                "detailed_status": item.get('MlsStatus'),
                "price": item.get('ListPrice'),
                "close_price": item.get('ClosePrice'),
                "close_date": parser.isoparse(item.get('CloseDate')).date() if item.get('CloseDate') else None,
                "is_published": True,
                "city": item.get('City'),
                "address": item.get('UnparsedAddress'),
                "zipcode": item.get('PostalCode'),
                "lat": item.get('Latitude'),
                "lon": item.get('Longitude'),
                "baths": safe_float(item.get('BathsTotal')),
                "beds": int(safe_float(item.get('BedroomsTotal'))) if item.get('BedroomsTotal') else None,
                "year_built": item.get('YearBuilt'),
                "photo_url": item.get('Photo1URL'),
                "list_agent_name": item.get('ListAgentFullName'),
                "buyer_agent_name": item.get('BuyerAgentFullName'),
                "listing_brokerage": item.get('ListOfficeName'),
                "property_type": item.get('PropertyType'),
                "public_remarks": item.get('PublicRemarks'),
                "rmls_dom": rmls_dom,
                "rmls_cdom": rmls_cdom
            }
            new_listing = Listing(**listing_data)
            db.add(new_listing)
            count_new += 1
    
    await db.commit()
    print(f"   💾 Batch Saved: {count_new} New | {count_updated} Updated")

async def run_history_sync():
    async with AsyncSessionLocal() as db:
        base_url = "https://resoapi.rmlsweb.com/reso/odata/Property"
        token = os.getenv("RMLS_TOKEN")
        headers = {
            "Authorization": f"Bearer {token}",
            "RESO-OData-Version": "4.0",
            "Accept": "application/json"
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            for year in range(START_YEAR, END_YEAR + 1):
                listings = await fetch_year(year, client, headers, base_url)
                if listings:
                    await save_batch(listings, db)
                print(f"✅ Finished {year}. Sleeping...")
                time.sleep(2) 

if __name__ == "__main__":
    asyncio.run(run_history_sync())