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

# Setup path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Listing, ListingImage
from app.database import AsyncSessionLocal
from sqlalchemy import select, delete, update
from sqlalchemy.orm import configure_mappers

configure_mappers()

def get_pst_now():
    return datetime.now(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None)

def safe_float(value):
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None

# Simple normalizer (Since we are only pulling Active, this is just a safeguard)
def normalize_status(std_status):
    if not std_status: return 'Active'
    s = std_status.lower()
    if 'closed' in s or 'sold' in s: return 'Sold'
    if 'pending' in s or 'contract' in s: return 'Pending'
    return 'Active'

async def sync_rmls_listings():
    async with AsyncSessionLocal() as db:
        base_url = "https://resoapi.rmlsweb.com/reso/odata/Property"
        token = os.getenv("RMLS_TOKEN")
        
        if not token:
            print("❌ Error: RMLS_TOKEN not found.")
            return

        # --- 1. EXPANDED FIELD LIST ---
        select_fields = [
            # Core
            "ListingId", "ListPrice", "City", "UnparsedAddress", 
            "BedroomsTotal", "BathsTotal", "Photo1URL", "Latitude", "Longitude", 
            "IDXAddressDisplayYn", "BuildingAreaTotal", "LotSizeSquareFeet", 
            "LotSizeAcres", "YearBuilt", "DaysOnMarket", "Media", "PublicRemarks", 
            "PropertyType", "PropertySubType", "StandardStatus", "MlsStatus", 
            "StatusChangeTimestamp", "PostalCode",
            
            # Agents
            "ListOfficeName", "ListAgentFullName", "BuyerAgentFullName", 
            "AttributionContact", 
            
            # New Details
            "TaxAnnualAmount", "AssociationFee", "AssociationYn", 
            "Cooling", "ElementarySchool", "MiddleOrJuniorSchool", # <--- Added
            "FuelDescription", "GarageSpaces", "GrossIncome", "Heating", 
            "HighSchool", "ListPriceHigh", "ListPriceLow", "MLSAreaMajor", 
            "Roof", "Sewer", "TaxLegalDescription", "Utilities", 
            "WaterSource", "Zoning"
        ]

        gorge_zips = [
            "97031", "97041", "97044", "97040", "97014", "97058", "97021", 
            "97028", "98672", "98605", "98651", "98635", "98650", "98648", "98617"
        ]

        # --- 2. SAFE FILTER (Active Only) ---
        # This matches your original working logic
        zip_filter = " or ".join([f"PostalCode eq '{z}'" for z in gorge_zips])
        
        # We use the robust Odata syntax just to be safe, but keep logic simple
        final_filter = f"({zip_filter}) and StandardStatus eq Odata.Models.StandardStatus'Active'"

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

        # --- DOWNLOAD LOOP ---
        all_listings = []
        url_to_fetch = base_url
        params_to_use = params
        page_count = 1

        print("🚀 Starting Sync (Active Listings Only + New Data)...")

        async with httpx.AsyncClient(timeout=60.0) as client:
            while url_to_fetch:
                print(f"   ⬇️ Page {page_count}...")
                try:
                    response = await client.get(url_to_fetch, headers=headers, params=params_to_use)
                    
                    if response.status_code != 200:
                        print(f"❌ Critical API Error: {response.status_code}")
                        print("👇 ERROR DETAILS 👇")
                        print(response.text) # <--- READ THIS IF IT FAILS
                        return 

                    data = response.json()
                    batch = data.get('value', [])
                    if not batch: break

                    all_listings.extend(batch)
                    next_link = data.get('@odata.nextLink')

                    if next_link:
                        url_to_fetch = next_link
                        params_to_use = None 
                        page_count += 1
                    else:
                        url_to_fetch = None

                except Exception as e:
                    print(f"❌ Exception: {e}")
                    return

        # --- UPDATE LOOP ---
        current_time_pst = get_pst_now()
        new_count = 0
        updated_count = 0
        
        print(f"\n📦 Processing {len(all_listings)} Listings...")

        for item in all_listings:
            mls_id = str(item.get('ListingId'))
            result = await db.execute(select(Listing).filter(Listing.mls_number == mls_id))
            existing_listing = result.scalars().first()

            raw_status_time = item.get('StatusChangeTimestamp')
            status_time_obj = parser.isoparse(raw_status_time).replace(tzinfo=None) if raw_status_time else None
            
            clean_status = normalize_status(item.get('StandardStatus'))

            listing_data = {
                # CORE
                "price": item.get('ListPrice'),
                "status": clean_status,
                "detailed_status": item.get('MlsStatus'),
                "status_date": status_time_obj,
                "days_on_market": item.get('DaysOnMarket'),
                "is_published": True,
                "last_updated": current_time_pst,
                
                # ADDRESS
                "city": item.get('City'),
                "address": item.get('UnparsedAddress'),
                "zipcode": item.get('PostalCode'),
                "lat": item.get('Latitude'),
                "lon": item.get('Longitude'),
                "is_address_exposed": item.get('IDXAddressDisplayYn'),
                
                # BASIC STATS
                "baths": safe_float(item.get('BathsTotal')),
                "beds": int(safe_float(item.get('BedroomsTotal'))) if item.get('BedroomsTotal') else None,
                "sqft": int(safe_float(item.get('BuildingAreaTotal'))) if item.get('BuildingAreaTotal') else None,
                "year_built": item.get('YearBuilt'),
                "acreage": safe_float(item.get('LotSizeAcres')),
                "lot_size_sqft": safe_float(item.get('LotSizeSquareFeet')),
                
                # AGENTS
                "listing_brokerage": item.get('ListOfficeName'),
                "list_agent_name": item.get('ListAgentFullName'),
                "buyer_agent_name": item.get('BuyerAgentFullName'), 
                "attribution_contact": item.get('AttributionContact'),
                
                # DESCRIPTIONS
                "public_remarks": item.get('PublicRemarks'),
                "property_type": item.get('PropertyType'),
                "property_sub_type": item.get('PropertySubType'),
                "tax_legal_description": item.get('TaxLegalDescription'),
                
                # FINANCIALS
                "tax_annual_amount": safe_float(item.get('TaxAnnualAmount')),
                "association_fee": safe_float(item.get('AssociationFee')),
                "association_yn": item.get('AssociationYn'),
                "gross_income": safe_float(item.get('GrossIncome')),
                "list_price_high": safe_float(item.get('ListPriceHigh')),
                "list_price_low": safe_float(item.get('ListPriceLow')),
                
                # DETAILS
                "garage_spaces": safe_float(item.get('GarageSpaces')),
                "cooling": _list_to_str(item.get('Cooling')), 
                "heating": _list_to_str(item.get('Heating')),
                "fuel_description": _list_to_str(item.get('FuelDescription')),
                "roof": _list_to_str(item.get('Roof')),
                "sewer": _list_to_str(item.get('Sewer')),
                "water_source": _list_to_str(item.get('WaterSource')),
                "utilities": _list_to_str(item.get('Utilities')),
                
                # SCHOOLS & AREA
                "elementary_school": item.get('ElementarySchool'),
                "middle_or_junior_school": item.get('MiddleOrJuniorSchool'), # <--- Added
                "high_school": item.get('HighSchool'),
                "mls_area_major": item.get('MLSAreaMajor'),
                "zoning": item.get('Zoning'),
            }

            if existing_listing:
                for key, value in listing_data.items():
                    setattr(existing_listing, key, value)
                updated_count += 1
                target_listing = existing_listing
            else:
                new_listing = Listing(mls_number=mls_id, is_new=True, **listing_data)
                db.add(new_listing)
                await db.flush()
                target_listing = new_listing
                print(f" [NEW]    MLS#: {mls_id} - {item.get('UnparsedAddress')}")

                new_count += 1

            # Media Sync
            media_data = item.get('Media', [])
            if media_data:
                await db.execute(delete(ListingImage).where(ListingImage.listing_id == target_listing.id))
                for idx, m in enumerate(media_data):
                    if m.get('MediaURL'):
                        db.add(ListingImage(listing_id=target_listing.id, url=m.get('MediaURL'), order=idx, is_private=m.get('PrivateYn')))

        # --- RECONCILIATION ---
        # Safe Version: Only marks Active listings as Inactive if they disappear
        if len(all_listings) > 0:
            reconcile_time = current_time_pst.replace(tzinfo=None)
            stale_result = await db.execute(
                update(Listing)
                .where(Listing.last_updated < reconcile_time)
                .where(Listing.status == 'Active') 
                .values(
                    status='Off-Market',        
                    is_published=False,         
                    last_updated=reconcile_time
                )
            )
            print(f"Reconciled {stale_result.rowcount} listings to Off-Market.")

        await db.commit()
        print(f"✅ Sync complete.")

def _list_to_str(val):
    if isinstance(val, list):
        return ", ".join(str(x) for x in val)
    return str(val) if val else None

if __name__ == "__main__":
    asyncio.run(sync_rmls_listings())