import asyncio
import csv
import uuid
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, Integer, Numeric, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID

Base = declarative_base()

class Listing(Base):
    __tablename__ = "listings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    mls_id = Column(String)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    price = Column(Integer)
    beds = Column(Integer)
    baths = Column(Numeric(3, 1))
    sqft = Column(Integer)
    status = Column(String)
    listed_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True))
    updated_at = Column(TIMESTAMP(timezone=True))


# Your remote Postgres connection
DATABASE_URL = "postgresql+asyncpg://postgres:1010Ann!23@143.110.228.226:5432/listing_alerts"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def parse_ts(value):
    """Convert a timestamp string from CSV to a datetime object."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def main():
    async with AsyncSessionLocal() as session:
        listings_to_add = []

        with open("listings.csv", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                listing = Listing(
                    mls_id=row.get("listingId"),
                    address=row.get("address"),
                    city=row.get("city"),
                    state=row.get("state"),
                    zip=row.get("zip"),
                    price=int(row["price"]) if row.get("price") else None,
                    beds=int(row["beds"]) if row.get("beds") else None,
                    baths=float(row["baths"]) if row.get("baths") else None,
                    sqft=int(row["sqft"]) if row.get("sqft") else None,
                    status=row.get("status", "active"),
                    listed_at=parse_ts(row.get("listed_at")),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                listings_to_add.append(listing)

        session.add_all(listings_to_add)
        await session.commit()
        print(f"Loaded {len(listings_to_add)} listings!")


if __name__ == "__main__":
    asyncio.run(main())