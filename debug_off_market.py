import os
import httpx
import asyncio
from dotenv import load_dotenv

load_dotenv()

# -----------------------------------------
# 🛠️ REPLACE THIS WITH ONE KNOWN EXPIRED MLS #
TARGET_MLS_ID = "571097060"  
# -----------------------------------------

async def inspect_listing():
    base_url = "https://resoapi.rmlsweb.com/reso/odata/Property"
    token = os.getenv("RMLS_TOKEN")
    
    if not token:
        print("❌ Error: RMLS_TOKEN not found.")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "RESO-OData-Version": "4.0",
        "Accept": "application/json"
    }

    # We ask for the specific ID and ignore all other filters
    params = {
        "$filter": f"ListingId eq '{TARGET_MLS_ID}'",
        "$select": "ListingId,StandardStatus,MlsStatus,StatusChangeTimestamp,ModificationTimestamp,PostalCode,UnparsedAddress"
    }

    print(f"🕵️ INSPECTING: {TARGET_MLS_ID}...")

    async with httpx.AsyncClient() as client:
        resp = await client.get(base_url, headers=headers, params=params)
        
        if resp.status_code != 200:
            print(f"❌ API Error: {resp.status_code} - {resp.text}")
            return

        data = resp.json().get('value', [])
        
        if not data:
            print("❌ Listing NOT FOUND in API. (Are you sure this MLS# exists in the RMLS API feed?)")
        else:
            item = data[0]
            print("\n✅ RAW DATA FOUND:")
            print("-" * 40)
            print(f"Address:             {item.get('UnparsedAddress')}")
            print(f"Zip Code:            {item.get('PostalCode')}")
            print(f"StandardStatus:      {item.get('StandardStatus')}")
            print(f"MlsStatus:           {item.get('MlsStatus')}")
            print(f"StatusChangeTime:    {item.get('StatusChangeTimestamp')}")
            print(f"ModificationTime:    {item.get('ModificationTimestamp')}")
            print("-" * 40)
            
            # DIAGNOSIS
            print("\n🔎 DIAGNOSIS:")
            # Check Zip
            user_zips = ["97031", "97041", "97044", "97040", "97014", "97058", "97021", "97028", "98672", "98605", "98651", "98635", "98650", "98648", "98617"]
            if item.get('PostalCode') not in user_zips:
                print(f"👉 PROBLEM FOUND: Zip {item.get('PostalCode')} is NOT in your 'gorge_zips' list!")
            
            # Check Status
            if item.get('StandardStatus') != 'Expired':
                print(f"👉 PROBLEM FOUND: API calls this '{item.get('StandardStatus')}', not 'Expired'. Update your filter!")

            # Check Time
            print(f"👉 CHECK DATE: Does StatusChangeTime look recent? If it's Null, use ModificationTime.")

if __name__ == "__main__":
    asyncio.run(inspect_listing())