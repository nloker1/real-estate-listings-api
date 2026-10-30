"""Create a fictional local admin workspace; refuses non-test environments."""
import asyncio
from datetime import datetime, timezone
import os
import sys

from scripts.seed_test_data import seed_url, listing_fixtures


async def seed(url):
    from sqlalchemy import delete, insert, text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models import Base, Lead, SavedSearch, Listing, EmailLog
    from app.services.email_events import record_event
    engine = create_async_engine(url, execution_options={"schema_translate_map": {None: "admin_demo"}})
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(72410320)"))
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS admin_demo"))
            await connection.run_sync(Base.metadata.create_all)
            for table in reversed(Base.metadata.sorted_tables):
                await connection.execute(delete(table))
            await connection.execute(insert(Lead), [
                {"id": 1, "email": "buyer@example.test", "name": "Synthetic Buyer", "is_unsubscribed": False},
                {"id": 2, "email": "paused@example.test", "name": "Paused Buyer", "is_unsubscribed": True},
                {"id": 3, "email": "delivery@example.test", "name": "Delivery Example", "is_unsubscribed": False},
            ])
            await connection.execute(insert(SavedSearch), [
                {"id": 1, "lead_id": 1, "criteria": {"cities": ["White Salmon"], "minPrice": 400000,
                 "maxPrice": 700000, "minBeds": 3, "minBaths": 2}, "is_active": True, "frequency": "instant"},
                {"id": 2, "lead_id": 2, "criteria": {"alert_type": "market", "city": "Hood River"},
                 "is_active": False, "frequency": "weekly"},
                {"id": 3, "lead_id": 3, "criteria": {"alert_type": "property", "mls_number": "SYNTHETIC-MATCH"},
                 "is_active": True, "frequency": "instant"},
            ])
            await connection.execute(insert(Listing), listing_fixtures())
            await connection.execute(insert(EmailLog), [
                {"id": 1, "search_id": 1, "listing_id": "SYNTHETIC-MATCH", "user_email": "buyer@example.test",
                 "message_id": "demo-accepted-1", "sent_at": datetime(2026, 1, 3, 12, tzinfo=timezone.utc)},
                {"id": 2, "search_id": 3, "listing_id": "SYNTHETIC-MATCH", "user_email": "delivery@example.test",
                 "message_id": "demo-accepted-2", "sent_at": datetime(2026, 1, 3, 12, tzinfo=timezone.utc)},
            ])
            sessions = async_sessionmaker(bind=connection, expire_on_commit=False,
                                          join_transaction_mode="create_savepoint")
            async with sessions() as db:
                for i, (message, kind, details) in enumerate([
                    ("demo-accepted-1", "email.delivered", {}),
                    ("demo-accepted-1", "email.opened", {}),
                    ("demo-accepted-1", "email.clicked", {"link": "https://gorgerealty.com/property/synthetic/SYNTHETIC-MATCH"}),
                    ("demo-accepted-2", "email.bounced", {}),
                ], start=1):
                    await record_event(db, {"provider_event_id": f"demo-event-{i}", "message_id": message,
                        "event_type": kind, "occurred_at": datetime(2026, 1, 3, 12, i, tzinfo=timezone.utc),
                        "details": details, "processing_status": "pending"})
                await db.commit()
            for table, last_id in (("leads", 3), ("saved_searches", 3), ("listings", 6), ("email_logs", 2)):
                await connection.execute(text("SELECT setval(pg_get_serial_sequence(:table_name, 'id'), :last_id, true)"),
                                         {"table_name": f"admin_demo.{table}", "last_id": last_id})
        print("Created admin_demo: 3 fictional clients, saved searches, synthetic emails and events. No emails sent.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        url = seed_url(os.environ)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    try:
        asyncio.run(seed(url))
    except Exception:
        print("Admin demo seed failed. Check local test PostgreSQL, permissions, and dependencies.", file=sys.stderr)
        raise SystemExit(1) from None
