import httpx
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()


async def check_keys():
    token = os.getenv("RMLS_TOKEN")
    print(token)
    headers = {"Authorization": f"Bearer {token}", "RESO-OData-Version": "4.0"}
    url = "https://resoapi.rmlsweb.com/reso/odata/Property"
    
    # Just get 1 record to see the keys
    params = {"$top": 1, "$filter": "CountyOrParish eq Odata.Models.CountyOrParish'Coos' and StandardStatus eq Odata.Models.StandardStatus'Active'"}
    
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers, params=params)
        if res.status_code == 200:
            first_listing = res.json()['value'][0]
            print("--- ACTUAL API KEYS ---")
            for key in sorted(first_listing.keys()):
                print(key)
        else:
            print(f"Error: {res.status_code}")

asyncio.run(check_keys())