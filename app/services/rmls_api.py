import sys
import os
import httpx
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dateutil import parser

# Load environment variables
load_dotenv()

# Setup path to find app folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Listing, ListingImage
from app.database import AsyncSessionLocal
from sqlalchemy import select, delete, update
from sqlalchemy.orm import configure_mappers

configure_mappers()

def get_pst_now():
    """Returns current PST time as a naive datetime object for DB compatibility."""
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
            print("Error: RMLS_TOKEN not found.")
            return

        # Explicitly defined fields based on your metadata
        select_fields = [
            "ListingId", "ListPrice", "City", "UnparsedAddress",
            "BedroomsTotal", "BathsTotal", "Photo1URL", "Latitude",
            "Longitude", "IDXAddressDisplayYn", "BuildingAreaTotal",
            "LotSizeSquareFeet", "LotSizeAcres", "YearBuilt", 
            "Media", "ListOfficeName", "PublicRemarks", 
            "PropertyType", "PropertySubType", "ListAgentFullName",
            "StandardStatus", "MlsStatus", "StatusChangeTimestamp"
        ]
        
        params = {
            "$filter": "CountyOrParish eq Odata.Models.CountyOrParish'Coos' and StandardStatus eq Odata.Models.StandardStatus'Active'",
            "$select": ",".join(select_fields),
            "$expand": "Media",
            "$top": 100 # Increased to ensure we capture all active listings
        }
        
        headers = {
            "Authorization": f"Bearer {token}",
            "RESO-OData-Version": "4.0",
            "Accept": "application/json"
        }

        try:
            print("Requesting active listings from RMLS...")
            async with httpx.AsyncClient() as client:
                response = await client.get(base_url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()
                listings = data.get('value', [])
                current_time_pst = get_pst_now()
                
                print(f"Processing {len(listings)} listings...")

                for item in listings:
                    mls_id = str(item.get('ListingId'))
                    
                    # 1. Check if listing exists
                    result = await db.execute(select(Listing).filter(Listing.mls_number == mls_id))
                    existing_listing = result.scalars().first()

                    # Data cleaning and parsing
                    raw_status_time = item.get('StatusChangeTimestamp')
                    status_time_obj = parser.isoparse(raw_status_time).replace(tzinfo=None) if raw_status_time else None
                    
                    baths_val = safe_float(item.get('BathsTotal'))
                    beds_val = int(safe_float(item.get('BedroomsTotal'))) if item.get('BedroomsTotal') else None
                    sqft_val = int(safe_float(item.get('BuildingAreaTotal'))) if item.get('BuildingAreaTotal') else None

                    # 2. Update or Create
                    if existing_listing:
                        # Map fields for update
                        existing_listing.price = item.get('ListPrice')
                        existing_listing.baths = baths_val
                        existing_listing.beds = beds_val
                        existing_listing.sqft = sqft_val
                        existing_listing.standard_status = item.get('StandardStatus')
                        existing_listing.mls_status = item.get('MlsStatus')
                        existing_listing.status_change_timestamp = status_time_obj
                        existing_listing.internal_status = 'Active' # Mark as Active if found in feed
                        existing_listing.last_updated = current_time_pst 
                        
                        target_listing = existing_listing
                    else:
                        new_listing = Listing(
                            mls_number=mls_id,
                            price=item.get('ListPrice'),
                            city=item.get('City'),
                            address=item.get('UnparsedAddress'),
                            baths=baths_val,
                            beds=beds_val,
                            sqft=sqft_val,
                            lat=item.get('Latitude'),
                            lon=item.get('Longitude'),
                            photo_url=item.get('Photo1URL'),
                            year_built=item.get('YearBuilt'),
                            acreage=safe_float(item.get('LotSizeAcres')),
                            lot_size_sqft=safe_float(item.get('LotSizeSquareFeet')),
                            is_address_exposed=item.get('IDXAddressDisplayYn'),
                            listing_brokerage=item.get('ListOfficeName'),
                            public_remarks=item.get('PublicRemarks'),
                            property_type=item.get('PropertyType'),
                            property_sub_type=item.get('PropertySubType'),
                            list_agent_name=item.get('ListAgentFullName'),
                            standard_status=item.get('StandardStatus'),
                            mls_status=item.get('MlsStatus'),
                            status_change_timestamp=status_time_obj,
                            internal_status='Active',
                            is_new=True,
                            last_updated=current_time_pst
                        )
                        db.add(new_listing)
                        await db.flush() 
                        target_listing = new_listing
                        print(f" [NEW]    MLS#: {mls_id} - {item.get('UnparsedAddress')}")

                    # 3. Media Handling
                    media_data = item.get('Media', [])
                    if media_data:
                        await db.execute(delete(ListingImage).where(ListingImage.listing_id == target_listing.id))
                        for idx, m in enumerate(media_data):
                            img_url = m.get('MediaURL')
                            if img_url:
                                db.add(ListingImage(listing_id=target_listing.id, url=img_url, order=idx))

                # --- 4. RECONCILIATION (Kill the Zombies) ---
                # Any listing currently 'Active' that wasn't updated in this run is now off-market
                if len(listings) > 0:
                    # Ensure current_time_pst is definitely naive
                    reconcile_time = current_time_pst.replace(tzinfo=None)

                    stale_result = await db.execute(
                        update(Listing)
                        .where(Listing.last_updated < reconcile_time)
                        .where(Listing.internal_status == 'Active')
                        .values(
                            internal_status='Inactive', 
                            standard_status='Off-Market',
                            # Force this to be naive to match the column type
                            last_updated=reconcile_time 
                        )
                    )
                    print(f"Reconciled {stale_result.rowcount} listings.")

                await db.commit()
                print(f"Sync complete at {current_time_pst} PST.")
            else:
                print(f"API Error {response.status_code}: {response.text}")

        except Exception as e:
            print(f"Sync failed: {e}")
            await db.rollback()

if __name__ == "__main__":
    asyncio.run(sync_rmls_listings())