import sys
import os
import httpx
import asyncio
from dotenv import load_dotenv
from datetime import datetime
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
            print("❌ Error: RMLS_TOKEN not found.")
            return

        # Explicitly defined fields based on your metadata
        select_fields = [
            "ListingId", "ListPrice", "City", "UnparsedAddress",
            "BedroomsTotal", "BathsTotal", "Photo1URL", "Latitude",
            "Longitude", "IDXAddressDisplayYn", "BuildingAreaTotal",
            "LotSizeSquareFeet", "LotSizeAcres", "YearBuilt", 
            "Media", "ListOfficeName", "PublicRemarks", 
            "PropertyType", "PropertySubType", "ListAgentFullName",
            "StandardStatus", "MlsStatus", "StatusChangeTimestamp",
            "AttributionContact", "PostalCode"
        ]

        gorge_zips = [
            "97031", # Hood River, OR
            "97041", # Parkdale/Mt Hood, OR
            "97044", # Odell, OR
            "97040", # Mosier, OR
            "97014", # Cascade Locks, OR
            "97058", # The Dalles, OR
            "97021", # Dufur, OR
            "98672", # White Salmon, WA
            "98605", # Bingen, WA
            "98651", # Underwood, WA
            "98635", # Lyle, WA
            "98650", # Trout Lake, WA
            "98648", # Stevenson, WA
            "98617", # Dallesport
            "97028", # Government Camp
            # "98620",  Goldendale, WA (Optional - might be too far east?) ]
            ]

        # 2. BUILD THE FILTER STRING DYNAMICALLY
        # This creates: (PostalCode eq '97031' or PostalCode eq '97041' or ...)
        zip_filter = " or ".join([f"PostalCode eq '{z}'" for z in gorge_zips])
        
        # Combined Filter: (Zip A or Zip B...) AND Status is Active
        final_filter = f"({zip_filter}) and StandardStatus eq Odata.Models.StandardStatus'Active'"

        # 3. SET THE PARAMS
        params = {
            "$filter": final_filter,
            "$select": ",".join(select_fields),
            "$expand": "Media",
            "$top": 250 
        }
        
        # Initial Parameters for the FIRST page
        params = {
            "$filter": final_filter,
            "$select": ",".join(select_fields),
            "$expand": "Media",
            "$top": 250 
        }
        
        headers = {
            "Authorization": f"Bearer {token}",
            "RESO-OData-Version": "4.0",
            "Accept": "application/json"
        }

        # --- 1. PAGINATION LOOP (THE DOWNLOADER) ---
        all_listings = []
        url_to_fetch = base_url
        params_to_use = params
        page_count = 1

        print("🚀 Starting RMLS Download...")

        async with httpx.AsyncClient(timeout=60.0) as client:
            while url_to_fetch:
                print(f"   ⬇️ Fetching Page {page_count}...")
                
                try:
                    response = await client.get(url_to_fetch, headers=headers, params=params_to_use)
                    
                    if response.status_code != 200:
                        print(f"❌ Critical API Error on Page {page_count}: {response.status_code}")
                        print(response.text)
                        return 

                    data = response.json()
                    batch = data.get('value', [])
                    
                    if not batch:
                        print("   ⚠️ Page returned empty list. Stopping download.")
                        break

                    count = len(batch)
                    print(f"   ✅ Got {count} listings.")
                    all_listings.extend(batch)

                    # CHECK FOR NEXT LINK
                    # RESO standard is usually '@odata.nextLink'
                    next_link = data.get('@odata.nextLink')

                    if next_link:
                        url_to_fetch = next_link
                        # CRITICAL: Next link already has params embedded, so we MUST clear ours
                        # otherwise we send conflicting parameters and break the API.
                        params_to_use = None 
                        page_count += 1
                    else:
                        print("   🏁 No Next Link found. Download complete.")
                        url_to_fetch = None # Breaks the loop

                except Exception as e:
                    print(f"❌ Exception in fetch loop: {e}")
                    return

        # --- 2. PROCESSING LOOP (THE UPDATER) ---
        # Now we process everything we downloaded
        listings = all_listings
        current_time_pst = get_pst_now()
        
        print(f"\n📦 Processing Total: {len(listings)} Listings...")
        
        if len(listings) == 0:
            print("❌ No listings found to process. Exiting.")
            return

        new_count = 0
        updated_count = 0

        for item in listings:
            mls_id = str(item.get('ListingId'))
            
            # A. Check if listing exists
            result = await db.execute(select(Listing).filter(Listing.mls_number == mls_id))
            existing_listing = result.scalars().first()

            # B. Data cleaning
            raw_status_time = item.get('StatusChangeTimestamp')
            status_time_obj = parser.isoparse(raw_status_time).replace(tzinfo=None) if raw_status_time else None
            
            baths_val = safe_float(item.get('BathsTotal'))
            beds_val = int(safe_float(item.get('BedroomsTotal'))) if item.get('BedroomsTotal') else None
            sqft_val = int(safe_float(item.get('BuildingAreaTotal'))) if item.get('BuildingAreaTotal') else None

            # C. Update or Create
            if existing_listing:
                existing_listing.price = item.get('ListPrice')
                existing_listing.baths = baths_val
                existing_listing.beds = beds_val
                existing_listing.sqft = sqft_val
                existing_listing.standard_status = item.get('StandardStatus')
                existing_listing.mls_status = item.get('MlsStatus')
                existing_listing.status_change_timestamp = status_time_obj
                existing_listing.internal_status = 'Active'
                existing_listing.last_updated = current_time_pst 
                target_listing = existing_listing
                updated_count += 1 
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
                    attribution_contact=item.get('AttributionContact'),
                    zipcode=item.get('PostalCode'),
                    is_new=True,
                    last_updated=current_time_pst
                )
                db.add(new_listing)
                await db.flush() 
                target_listing = new_listing
                print(f" [NEW]    MLS#: {mls_id} - {item.get('UnparsedAddress')}")
                new_count += 1 

            # D. Media Handling
            media_data = item.get('Media', [])
            if media_data:
                await db.execute(delete(ListingImage).where(ListingImage.listing_id == target_listing.id))
                for idx, m in enumerate(media_data):
                    img_url = m.get('MediaURL')
                    is_private_val = m.get('PrivateYn', None)
                    if img_url:
                        db.add(ListingImage(listing_id=target_listing.id, url=img_url, order=idx, is_private=is_private_val))

        # --- 3. SUMMARY & RECONCILIATION ---
        # NOTE: This section is OUTSIDE the 'for' loop.
        print(f"SUMMARY: {new_count} New | {updated_count} Updated | {len(listings)} Processed")

        # Reconciliation: Mark missing Active listings as Off-Market
        if len(listings) > 0:
            reconcile_time = current_time_pst.replace(tzinfo=None)
            stale_result = await db.execute(
                update(Listing)
                .where(Listing.last_updated < reconcile_time)
                .where(Listing.internal_status == 'Active')
                .values(
                    internal_status='Inactive', 
                    standard_status='Off-Market',
                    last_updated=reconcile_time 
                )
            )
            print(f"Reconciled {stale_result.rowcount} listings.")

        await db.commit()
        print(f"✅ Sync complete at {current_time_pst} PST.")

if __name__ == "__main__":
    asyncio.run(sync_rmls_listings())