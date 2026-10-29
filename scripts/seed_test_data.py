"""Seed fictional alert fixtures: python -m scripts.seed_test_data.

Only the seed_alerts schema of the guarded local test database is modified.
No application database module, dotenv, email, or RMLS integration is imported.
"""

import asyncio
from datetime import datetime, timedelta
import os
import sys

from app.database_config import test_database_url


SCHEMA = "seed_alerts"


def seed_url(environ):
    if environ.get("APP_ENV", "").strip().lower() != "test":
        raise ValueError("Seeding requires APP_ENV=test")
    return test_database_url(environ)


def listing_fixtures():
    # Fixed naive UTC timestamps match the current model's timestamp types.
    created = datetime(2026, 1, 2, 12)
    scenarios = (
        ("MATCH", {}),
        ("TOO_EXPENSIVE", {"price": 900000}),
        ("WRONG_CITY", {"city": "TheDalles", "zipcode": "97058"}),
        ("PENDING", {"status": "Pending"}),
        ("UNPUBLISHED", {"is_published": False}),
        ("TOO_FEW_BEDS", {"beds": 1}),
    )
    return [
        dict(
            {
                "id": index,
                "mls_number": f"SYNTHETIC-{scenario}",
                "address": f"{index} Fictional Test Lane",
                "city": "WhiteSalmon",
                "zipcode": "98672",
                "price": 550000,
                "beds": 3,
                "baths": 2.0,
                "sqft": 1800,
                "acreage": 0.25,
                "property_type": "Residential",
                "status": "Active",
                "is_published": True,
                "is_address_exposed": True,
                "is_new": True,
                "created_at": created,
                "last_updated": created,
            },
            **overrides,
        )
        for index, (scenario, overrides) in enumerate(scenarios, start=1)
    ]


async def seed(url):
    # Import dependencies only after configuration has passed validation.
    from sqlalchemy import delete, insert, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.models import Base, Lead, Listing, SavedSearch

    engine = create_async_engine(
        url, execution_options={"schema_translate_map": {None: SCHEMA}}
    )
    try:
        async with engine.begin() as connection:
            # Serialize simultaneous seed runs; all changes share one transaction.
            await connection.execute(text("SELECT pg_advisory_xact_lock(72410319)"))
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS seed_alerts"))
            await connection.run_sync(Base.metadata.create_all)
            for table in reversed(Base.metadata.sorted_tables):
                await connection.execute(delete(table))

            created = datetime(2026, 1, 1, 12)
            await connection.execute(insert(Lead.__table__), [{
                "id": 1,
                "email": "buyer@example.test",
                "name": "Synthetic Buyer",
                "is_unsubscribed": False,
                "unsubscribe_token": "synthetic-test-token-never-use-in-production",
                "created_at": created,
            }])
            await connection.execute(insert(SavedSearch.__table__), [{
                "id": 1,
                "lead_id": 1,
                "criteria": {
                    "cities": ["White Salmon"],
                    "minPrice": 400000,
                    "maxPrice": 700000,
                    "minBeds": 3,
                    "minBaths": 2,
                    "propertyType": "Residential",
                },
                "frequency": "instant",
                "is_active": True,
                "created_at": created,
                "last_alert_sent": created + timedelta(hours=1),
            }])
            await connection.execute(insert(Listing.__table__), listing_fixtures())
            # Explicit fixture IDs must not leave autoincrement sequences behind.
            for table in (Lead.__table__, SavedSearch.__table__, Listing.__table__):
                await connection.execute(text(
                    "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                    ":last_id, true)"
                ), {
                    "table_name": f"{SCHEMA}.{table.name}",
                    "last_id": 6 if table.name == "listings" else 1,
                })
        print("Seeded seed_alerts: 1 fictional lead, 1 saved search, 6 listings, 0 deliveries.")
        print("Expected eligible listing: SYNTHETIC-MATCH. No emails sent.")
    finally:
        await engine.dispose()


def main():
    try:
        url = seed_url(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        asyncio.run(seed(url))
    except Exception:
        # Driver exceptions can include credentials or submitted values.
        print("Test seed failed; transaction rolled back. Check local PostgreSQL, "
              "dependencies, and test-schema permissions.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
