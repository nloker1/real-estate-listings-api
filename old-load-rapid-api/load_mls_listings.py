import csv
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, Integer, Numeric, Float, text
from sqlalchemy.dialects.postgresql import UUID
from geopy.geocoders import Nominatim
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()
geolocator = Nominatim(
    user_agent="gorge_listings_loader",
    scheme="http"
)

class Listing(Base):
    __tablename__ = "listings"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    mls_number = Column(String)
    status = Column(String)
    address = Column(String)
    city = Column(String)
    price = Column(Numeric)
    beds = Column(Integer)
    baths = Column(Numeric)
    sqft = Column(Integer)
    year_built = Column(Integer)
    acres = Column(Numeric)
    lot_size = Column(String)
    style = Column(String)
    lat = Column(Float)
    lon = Column(Float)

engine = create_async_engine(DATABASE_URL)
SessionLocal = sessionmaker(engine, class_=AsyncSession)

def parse_int(val):
    try:
        return int(val)
    except:
        return None

def parse_float(val):
    try:
        return float(val)
    except:
        return None

def geocode(address, city):
    try:
        loc = geolocator.geocode(f"{address}, {city}, WA")
        if loc:
            return loc.latitude, loc.longitude
    except Exception as e:
        print("Geocode error:", e)
    return None, None

async def main():
    async with SessionLocal() as session:
        with open("mls-export-full.csv", newline="", encoding="cp1252") as f:
            reader = csv.DictReader(f)

            listings = []

            for row in reader:
                address = row["Address"]
                city = row["City"]

                lat, lon = geocode(address, city)

                if not lat or not lon:
                    print(f"Skipping (no geocode): {address}, {city}")
                    continue

                listings.append(
                    Listing(
                        mls_number=row["ML#"],
                        status=row["Status"],
                        address=address,
                        city=city,
                        price=parse_float(row["List Price"]),
                        beds=parse_int(row["Beds"]),
                        baths=parse_float(row["Baths"]),
                        sqft=parse_int(row["Tot Sqft"]),
                        year_built=parse_int(row["YrBlt"]),
                        acres=parse_float(row["# Acres"]),
                        lot_size=row["Lot Size"],
                        style=row["Style"],
                        lat=lat,
                        lon=lon
                    )
                )

                # throttle geocoding (VERY important)
                await asyncio.sleep(1)

            session.add_all(listings)
            await session.commit()

    print(f"Loaded {len(listings)} listings")

asyncio.run(main())