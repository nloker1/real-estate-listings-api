import sys
import os
import httpx
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo # Standard in Python 3.9+

# Load environment variables from .env
load_dotenv()

# Setup path to find app folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Listing, ListingImage
from app.database import AsyncSessionLocal 
from sqlalchemy import select, delete
from sqlalchemy.orm import configure_mappers

configure_mappers() 

# Helper function to get current time in PST (Naive for Postgres compatibility)
def get_pst_now():
    return datetime.now(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None)

def safe_float(value):
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None

async def sync_rmls_listings():
    async with AsyncSessionLocal() as db:
        base_url = "https://resoapi.rmlsweb.com/reso/odata/Property"
        token = os.getenv("RMLS_TOKEN")
        
        if not token:
            print("Error: RMLS_TOKEN not found in environment variables.")
            return

        select_fields = [
            "ListingId", "ListPrice", "City", "UnparsedAddress",
            "BedroomsTotal", "BathsTotal", "Photo1URL", "Latitude",
            "Longitude", "IDXAddressDisplayYn", "BuildingAreaTotal",
            "YearBuilt", "Media", "ListOfficeName", "PublicRemarks"
        ]
        
        params = {
            "$filter": "CountyOrParish eq Odata.Models.CountyOrParish'Coos' and StandardStatus eq Odata.Models.StandardStatus'Active'",
            "$select": ",".join(select_fields),
            "$expand": "Media",
            "$top": 30
        }
                
        headers = {
            "Authorization": f"Bearer {token}",
            "RESO-OData-Version": "4.0",
            "Accept": "application/json"
        }

        try:
            print("Requesting listings from RMLS (Async)...")
            async with httpx.AsyncClient() as client:
                response = await client.get(base_url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()
                listings = data.get('value', [])
                
                # Capture current time once for this sync batch
                current_time_pst = get_pst_now()
                
                for item in listings:
                    mls_id = str(item.get('ListingId'))
                    
                    # 1. Check if listing exists
                    result = await db.execute(select(Listing).filter(Listing.mls_number == mls_id))
                    existing_listing = result.scalars().first()

                    # Extract and cast the numeric values safely
                    raw_baths = item.get('BathsTotal')
                    raw_beds = item.get('BedroomsTotal')
                    raw_sqft = item.get('BuildingAreaTotal')

                    # Use the helper to ensure they are floats/ints
                    baths_val = safe_float(raw_baths)
                    beds_val = int(safe_float(raw_beds)) if raw_beds else None
                    sqft_val = int(safe_float(raw_sqft)) if raw_sqft else None

                    # 2. Update or Create
                    if existing_listing:
                        existing_listing.price = item.get('ListPrice')
                        existing_listing.last_updated = current_time_pst # PST Timestamp
                        existing_listing.baths = baths_val # Now a float
                        target_listing = existing_listing
                        print(f"Updated: {mls_id}")
                    else:
                        new_listing = Listing(
                            mls_number=mls_id,
                            price=item.get('ListPrice'),
                            city=item.get('City'),
                            address=item.get('UnparsedAddress'),
                            baths=baths_val, # Now a float
                            beds=beds_val,
                            lat=item.get('Latitude'),
                            lon=item.get('Longitude'),
                            photo_url=item.get('Photo1URL'),
                                sqft=sqft_val,
                            year_built=item.get('YearBuilt'),
                            is_address_exposed=item.get('IDXAddressDisplayYn'),
                            listing_brokerage=item.get('ListOfficeName'),
                            public_remarks=item.get('PublicRemarks'),
                            is_new=True,
                            last_updated=current_time_pst # PST Timestamp
                        )
                        db.add(new_listing)
                        await db.flush() 
                        target_listing = new_listing
                        print(f"Added New: {mls_id}")

                    # 3. Handle Media
                    media_data = item.get('Media', [])
                    if media_data:
                        await db.execute(delete(ListingImage).where(ListingImage.listing_id == target_listing.id))
                        for idx, m in enumerate(media_data):
                            img_url = m.get('MediaURL')
                            if img_url:
                                db.add(ListingImage(
                                    listing_id=target_listing.id,
                                    url=img_url,
                                    order=idx
                                ))

                await db.commit()
                print(f"Sync complete at {current_time_pst} PST.")
            else:
                print(f"Error {response.status_code}: {response.text}")

        except Exception as e:
            print(f"Error: {e}")
            await db.rollback()

if __name__ == "__main__":
    asyncio.run(sync_rmls_listings())