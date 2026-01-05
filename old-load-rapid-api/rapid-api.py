import requests
import csv
import time

# ------------------------------- CONFIG -------------------------------
RAPIDAPI_KEY = "aec1e783d2msh9c2eea2c99a4f7bp15c73cjsn5c51ea2fab0f"  # Replace!

BASE_URL = "https://redfin-com-data.p.rapidapi.com/properties/search-sale"

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": "redfin-com-data.p.rapidapi.com"
}

PARAMS = {
    "regionId": "6_2446",   # Or try "6_2338" for broader Portland/Hood River area
    "limit": 100,
    "offset": 0
}

TARGET_COUNT = 100
# ---------------------------------------------------------------------

def fetch_listings():
    all_listings = []
    offset = 0
    fetched = 0

    print("Fetching listings from Redfin API...")

    while fetched < TARGET_COUNT:
        PARAMS["offset"] = offset

        response = requests.get(BASE_URL, headers=HEADERS, params=PARAMS)

        if response.status_code != 200:
            print(f"Error {response.status_code}: {response.text}")
            break

        data = response.json()
        print("Top-level keys:", list(data.keys()))  # Debug

        listings_data = data.get("data", [])
        
        # Handle both list and dict cases
        if isinstance(listings_data, dict):
            items = listings_data.items()
        elif isinstance(listings_data, list):
            items = [(i, item) for i, item in enumerate(listings_data)]
        else:
            print("Unexpected data format")
            break

        if not items:
            print("No listings found in this response.")
            break

        for key, value in items:
            if fetched >= TARGET_COUNT:
                break

            home_data = value.get("homeData", {}) if isinstance(value, dict) else {}
            if not home_data:
                continue

            address_info = home_data.get("addressInfo", {})
            price_info = home_data.get("priceInfo", {})

            listing = {
                "listingId": home_data.get("listingId"),
                "propertyId": home_data.get("propertyId"),
                "address": f"{address_info.get('formattedStreetLine', '')}, {address_info.get('city', '')}, {address_info.get('state', '')} {address_info.get('zip', '')}",
                "price": price_info.get("amount"),
                "beds": home_data.get("beds"),
                "baths": home_data.get("baths"),
                "sqft": home_data.get("sqftInfo", {}).get("amount"),
                "lat": address_info.get("centroid", {}).get("centroid", {}).get("latitude"),
                "lon": address_info.get("centroid", {}).get("centroid", {}).get("longitude"),
                "url": home_data.get("url"),
                "photo_url": (home_data.get("photos", {}).get("smallPhotos") or [None])[0],  # First photo
                "yearBuilt": home_data.get("yearBuilt", {}).get("yearBuilt"),
                "lotSize": home_data.get("lotSize", {}).get("amount")
            }

            all_listings.append(listing)
            fetched += 1

        print(f"Fetched {fetched}/{TARGET_COUNT} listings this page...")

        if len(items) < PARAMS["limit"]:
            print("Fewer than limit – likely end of results.")
            break

        offset += PARAMS["limit"]
        time.sleep(1)

    return all_listings

def save_to_csv(listings, filename="listings.csv"):
    if not listings:
        print("No data to save.")
        return

    keys = listings[0].keys()
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(listings)

    print(f"\nSaved {len(listings)} listings to {filename}")


if __name__ == "__main__":
    listings = fetch_listings()
    save_to_csv(listings)