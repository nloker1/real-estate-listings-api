import sys
import os
import httpx
import asyncio
from dotenv import load_dotenv  # <--- Add this

# Load environment variables from .env
load_dotenv()

# Setup path to find app folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Listing, ListingImage
from app.database import AsyncSessionLocal # Matches your file exactly now
from sqlalchemy import select, delete
from sqlalchemy.orm import configure_mappers
configure_mappers() 


async def sync_rmls_listings():
    async with AsyncSessionLocal() as db:
        base_url = "https://resoapi.rmlsweb.com/reso/odata/Property"
        
        # Pull from .env instead of hardcoding
        token = os.getenv("RMLS_TOKEN")
        
        if not token:
            print("Error: RMLS_TOKEN not found in environment variables.")
            return

        select_fields = [
            "ListingId",
            "ListPrice",
            "City",
            "UnparsedAddress",
            "BedroomsTotal",
            "BathsTotal",
            "Photo1URL",
            "Latitude",
            "Longitude",
            "IDXAddressDisplayYn",
            "BuildingAreaTotal",
            "YearBuilt",
            "Media",
            "ListOfficeName",
            "PublicRemarks"
        ]
        
        params = {
                    "$filter": "CountyOrParish eq Odata.Models.CountyOrParish'Coos' and StandardStatus eq Odata.Models.StandardStatus'Active'",
                    "$select": ",".join(select_fields),
                    "$expand": "Media",  # <--- ADD THIS LINE
                    "$top": 10
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
                
            for item in listings:
                mls_id = str(item.get('ListingId'))
                
                # 1. First, check if listing exists
                result = await db.execute(select(Listing).filter(Listing.mls_number == mls_id))
                existing_listing = result.scalars().first()

                # 2. Update or Create the listing object
                if existing_listing:
                    existing_listing.price = item.get('ListPrice')
                    existing_listing.last_updated = datetime.utcnow() # Explicitly update the time
                    target_listing = existing_listing # Reference for the images
                    print(f"Updated: {mls_id}")
                else:
                    new_listing = Listing(
                        mls_number=mls_id,
                        price=item.get('ListPrice'),
                        city=item.get('City'),
                        address=item.get('UnparsedAddress'),
                        baths=item.get('BathsTotal'),
                        beds=item.get('BedroomsTotal'),
                        lat=item.get('Latitude'),
                        lon=item.get('Longitude'),
                        photo_url=item.get('Photo1URL'),
                        sqft=item.get('BuildingAreaTotal'),
                        year_built=item.get('YearBuilt'),
                        is_address_exposed=item.get('IDXAddressDisplayYn'),
                        listing_brokerage=item.get('ListOfficeName'),
                        public_remarks=item.get('PublicRemarks'), # <--- ADD THIS
                        is_new=True 
                    )
                    db.add(new_listing)
                    await db.flush() # This gives us the new ID without committing yet
                    target_listing = new_listing
                    print(f"Added New: {mls_id}")

                # 3. Now handle the Media (since we definitely have a target_listing.id now)
                media_data = item.get('Media', [])
                if media_data:
                    # Clear old images for this specific listing to prevent duplicates
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
                print("Sync complete.")
            else:
                print(f"Error {response.status_code}: {response.text}")

        except Exception as e:
            print(f"Error: {e}")
            await db.rollback()

if __name__ == "__main__":
    # This is how you run an async script from the terminal
    asyncio.run(sync_rmls_listings())