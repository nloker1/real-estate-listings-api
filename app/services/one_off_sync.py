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
# Assuming we are in /home/node/.openclaw/workspace/real-estate-listings-api/app/services/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Listing, ListingImage
from app.database import AsyncSessionLocal
from sqlalchemy import select, delete
from sqlalchemy.orm import configure_mappers

configure_mappers()

def get_pst_now():
    """Returns current PST time as a naive datetime object."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None)

def safe_float(value):
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None

def _list_to_str(val):
    """Helper to convert lists (like ['Gas', 'Wood']) to strings."""
    if isinstance(val, list):
        return ", ".join(str(x) for x in val)
    return str(val) if val else None

def normalize_status(std_status):
    if not std_status:
        return 'Active'
    s = std_status.lower()
    if 'closed' in s or 'sold' in s:
        return 'Sold'
    if 'pending' in s or 'contract' in s or 'activeundercontract' in s:
        return 'Pending'
    if 'expired' in s or 'withdrawn' in s or 'canceled' in s:
        return 'Off-Market'
    return 'Active'

async def fetch_one_off_listings(mls_numbers):
    async with AsyncSessionLocal() as db:
        base_url = "https://resoapi.rmlsweb.com/reso/odata/Property"
        token = os.getenv("RMLS_TOKEN")
        
        if not token:
            print("❌ Error: RMLS_TOKEN not found.")
            return

        select_fields = [
            "ListingId", "ListPrice", "ClosePrice", "CloseDate", "City", 
            "UnparsedAddress", "BedroomsTotal", "BathsTotal", "Photo1URL", 
            "Latitude", "Longitude", "IDXAddressDisplayYn", "BuildingAreaTotal",
            "LotSizeSquareFeet", "LotSizeAcres", "YearBuilt", "DaysOnMarket",
            "Media", "PublicRemarks", "PropertyType", "PropertySubType",
            "StandardStatus", "MlsStatus", "StatusChangeTimestamp", "PostalCode",
            "ListOfficeName", "ListAgentFullName", "BuyerAgentFullName", 
            "AttributionContact", "TaxAnnualAmount", "AssociationFee", "AssociationYn", 
            "Cooling", "ElementarySchool", "MiddleOrJuniorSchool", 
            "FuelDescription", "GarageSpaces", "GrossIncome", "Heating", 
            "HighSchool", "ListPriceHigh", "ListPriceLow", "MLSAreaMajor", 
            "Roof", "Sewer", "TaxLegalDescription", "Utilities", "WaterSource", "Zoning"
        ]

        # Construct filter for specific MLS numbers
        mls_filter = " or ".join([f"ListingId eq '{m}'" for m in mls_numbers])
        
        params = {
            "$filter": mls_filter,
            "$select": ",".join(select_fields),
            "$expand": "Media"
        }
        
        headers = {
            "Authorization": f"Bearer {token}",
            "RESO-OData-Version": "4.0",
            "Accept": "application/json"
        }

        print(f"🚀 Fetching specific MLS numbers: {', '.join(mls_numbers)}")

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.get(base_url, headers=headers, params=params)
                if response.status_code != 200:
                    print(f"❌ Critical API Error: {response.status_code}")
                    print(response.text)
                    return 

                data = response.json()
                listings_to_process = data.get('value', [])
                if not listings_to_process:
                    print("⚠️ No listings found for these MLS numbers.")
                    return

            except Exception as e:
                print(f"❌ Exception: {e}")
                return

        current_time_pst = get_pst_now()
        new_count = 0
        updated_count = 0
        
        for item in listings_to_process:
            mls_id = str(item.get('ListingId'))
            result = await db.execute(select(Listing).filter(Listing.mls_number == mls_id))
            existing_listing = result.scalars().first()

            raw_status_time = item.get('StatusChangeTimestamp')
            status_time_obj = parser.isoparse(raw_status_time).replace(tzinfo=None) if raw_status_time else None
            
            close_date = None
            if item.get('CloseDate'):
                 close_date = parser.isoparse(item.get('CloseDate')).date()

            clean_status = normalize_status(item.get('StandardStatus'))
            
            # For one-off adds, we likely want them visible even if Sold/Off-market? 
            # The prompt says "I have a few listings that I sold that aren't showing up".
            # Usually 'Sold' listings are visible in 'Recently Sold' sections.
            is_published = True 

            listing_data = {
                "price": item.get('ListPrice'),
                "status": clean_status,
                "detailed_status": item.get('MlsStatus'),
                "status_date": status_time_obj,
                "close_price": item.get('ClosePrice') if clean_status == 'Sold' else None,
                "close_date": close_date,
                "days_on_market": item.get('DaysOnMarket'),
                "is_published": is_published,
                "last_updated": current_time_pst,
                "city": item.get('City'),
                "address": item.get('UnparsedAddress'),
                "zipcode": item.get('PostalCode'),
                "lat": item.get('Latitude'),
                "lon": item.get('Longitude'),
                "is_address_exposed": item.get('IDXAddressDisplayYn'),
                "baths": safe_float(item.get('BathsTotal')),
                "beds": int(safe_float(item.get('BedroomsTotal'))) if item.get('BedroomsTotal') else None,
                "sqft": int(safe_float(item.get('BuildingAreaTotal'))) if item.get('BuildingAreaTotal') else None,
                "year_built": item.get('YearBuilt'),
                "acreage": safe_float(item.get('LotSizeAcres')),
                "lot_size_sqft": safe_float(item.get('LotSizeSquareFeet')),
                "listing_brokerage": item.get('ListOfficeName'),
                "list_agent_name": item.get('ListAgentFullName'),
                "buyer_agent_name": item.get('BuyerAgentFullName'), 
                "attribution_contact": item.get('AttributionContact'),
                "public_remarks": item.get('PublicRemarks'),
                "property_type": item.get('PropertyType'),
                "property_sub_type": item.get('PropertySubType'),
                "tax_legal_description": item.get('TaxLegalDescription'),
                "tax_annual_amount": safe_float(item.get('TaxAnnualAmount')),
                "association_fee": safe_float(item.get('AssociationFee')),
                "association_yn": item.get('AssociationYn'),
                "gross_income": safe_float(item.get('GrossIncome')),
                "list_price_high": safe_float(item.get('ListPriceHigh')),
                "list_price_low": safe_float(item.get('ListPriceLow')),
                "garage_spaces": safe_float(item.get('GarageSpaces')),
                "cooling": _list_to_str(item.get('Cooling')), 
                "heating": _list_to_str(item.get('Heating')),
                "fuel_description": _list_to_str(item.get('FuelDescription')),
                "roof": _list_to_str(item.get('Roof')),
                "sewer": _list_to_str(item.get('Sewer')),
                "water_source": _list_to_str(item.get('WaterSource')),
                "utilities": _list_to_str(item.get('Utilities')),
                "elementary_school": item.get('ElementarySchool'),
                "middle_or_junior_school": item.get('MiddleOrJuniorSchool'),
                "high_school": item.get('HighSchool'),
                "mls_area_major": item.get('MLSAreaMajor'),
                "zoning": item.get('Zoning'),
                "photo_url": item.get('Photo1URL'),
            }

            if existing_listing:
                for key, value in listing_data.items():
                    setattr(existing_listing, key, value)
                updated_count += 1
                target_listing = existing_listing
                print(f" [UPDATED] MLS#: {mls_id} - {item.get('UnparsedAddress')}")
            else:
                new_listing = Listing(
                    mls_number=mls_id, 
                    is_new=True, 
                    created_at=current_time_pst,
                    **listing_data
                )
                db.add(new_listing)
                await db.flush()
                target_listing = new_listing
                print(f" [NEW]     MLS#: {mls_id} - {item.get('UnparsedAddress')}")
                new_count += 1

            # Media Sync
            media_data = item.get('Media', [])
            if media_data:
                await db.execute(delete(ListingImage).where(ListingImage.listing_id == target_listing.id))
                for idx, m in enumerate(media_data):
                    url = m.get('MediaURL')
                    if url:
                        db.add(ListingImage(listing_id=target_listing.id, url=url, order=idx, is_private=m.get('PrivateYn')))

        await db.commit()
        print(f"\n✅ Done! Summary: {new_count} New | {updated_count} Updated")

if __name__ == "__main__":
    target_mls = ["222430265", "184494982", "448136436"]
    asyncio.run(fetch_one_off_listings(target_mls))
