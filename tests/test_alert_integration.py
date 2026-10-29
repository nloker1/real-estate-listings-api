"""Opt-in local PostgreSQL test; worker commits are rolled back after testing."""

from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timedelta
import io
import os
import socket
import unittest
from uuid import uuid4
from unittest.mock import patch
from urllib.parse import urlsplit
from contextlib import redirect_stdout

from app.database_config import test_database_url
from alert_fakes import FakeEmailSender


@contextmanager
def database_only_network(url):
    """Allow only this test database's loopback port, including DNS checks."""
    target = urlsplit(url)
    port = target.port or 5432
    blocked = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def check(host, requested_port):
        if host not in ("localhost", "127.0.0.1", "::1") or int(requested_port) != port:
            blocked.append("external network attempt")
            raise AssertionError("Only the local test database connection is permitted")

    def connect(sock, address):
        check(address[0], address[1])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address[0], address[1])
        return original_connect_ex(sock, address)

    def resolve(host, service, *args, **kwargs):
        check(host, service)
        return original_resolve(host, service, *args, **kwargs)

    with patch.object(socket.socket, "connect", connect), \
         patch.object(socket.socket, "connect_ex", connect_ex), \
         patch.object(socket, "getaddrinfo", resolve):
        yield blocked


@asynccontextmanager
async def alert_database():
    """Fresh current-model schema; DDL and worker commits roll back together."""
    if os.getenv("APP_ENV", "").strip().lower() != "test":
        raise AssertionError("Integration tests require APP_ENV=test")
    url = test_database_url(os.environ)
    with database_only_network(url) as blocked:
        from sqlalchemy import insert, text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.models import Base, Lead, Listing, SavedSearch
        from scripts.seed_test_data import listing_fixtures

        schema = "alert_regression_" + uuid4().hex
        engine = create_async_engine(url, execution_options={"schema_translate_map": {None: schema}})
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                    await connection.run_sync(Base.metadata.create_all)
                    await connection.execute(insert(Lead), [{
                        "id": 1, "email": "buyer@example.test", "name": "Synthetic Buyer",
                        "is_unsubscribed": False, "unsubscribe_token": "synthetic-token",
                        "created_at": datetime(2026, 1, 1, 12),
                    }])
                    await connection.execute(insert(SavedSearch), [{
                        "id": 1, "lead_id": 1, "is_active": True, "frequency": "instant",
                        "criteria": {"cities": ["White Salmon"], "minPrice": 400000,
                                     "maxPrice": 700000, "minBeds": 3, "minBaths": 2,
                                     "propertyType": "Residential"},
                        "created_at": datetime(2026, 1, 1, 12),
                        "last_alert_sent": datetime(2026, 1, 1, 13),
                    }])
                    await connection.execute(insert(Listing), listing_fixtures())
                    sessions = async_sessionmaker(bind=connection, expire_on_commit=False,
                                                  join_transaction_mode="create_savepoint")
                    yield connection, sessions
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()
            if blocked:
                raise AssertionError("Worker attempted forbidden external network access")


async def run_worker(sessions, sender, at=datetime(2026, 1, 3), **kwargs):
    from app.services.alert_worker import process_alerts
    with redirect_stdout(io.StringIO()):
        await process_alerts(sessions, sender, now=lambda: at, delay=0, **kwargs)


@unittest.skipUnless(os.getenv("RUN_ALERT_DB_TESTS") == "1", "opt-in local database test")
class AlertIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_delivery_failure_is_retried_without_resending_success(self):
        await self.check_partial_delivery_retry(FakeEmailSender(reject_first=True))

    async def test_provider_exception_is_retried_without_resending_success(self):
        await self.check_partial_delivery_retry(FakeEmailSender(raise_first=True))

    async def check_partial_delivery_retry(self, sender):
        from sqlalchemy import select, update
        from app.models import EmailLog, Listing
        async with alert_database() as (connection, sessions):
            candidates = {"SYNTHETIC-MATCH", "SYNTHETIC-TOO_EXPENSIVE"}
            await connection.execute(update(Listing).where(
                Listing.mls_number.in_(candidates)).values(price=550000))
            def listing_id(message):
                matches = [mls for mls in candidates if f'/{mls}"' in message["html"]]
                self.assertEqual(len(matches), 1)
                return matches[0]
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.attempts), 2)
            failed, succeeded = map(listing_id, sender.attempts)
            self.assertNotEqual(failed, succeeded)
            logs = (await connection.execute(select(EmailLog.listing_id, EmailLog.message_id))).all()
            self.assertEqual(logs, [(succeeded, "fake-1")])
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.attempts), 3, "Failed match was lost after a later success")
            self.assertEqual(listing_id(sender.attempts[2]), failed)
            logs = (await connection.execute(select(EmailLog.listing_id, EmailLog.message_id))).all()
            self.assertCountEqual(logs, [(succeeded, "fake-1"), (failed, "fake-2")])
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.attempts), 3)

    async def test_match_delivery_and_repeat_run(self):
        from sqlalchemy import select, update
        from app.models import EmailLog, SavedSearch
        async with alert_database() as (connection, sessions):
            sender = FakeEmailSender()
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.messages), 1)
            self.assertEqual(sender.messages[0]["to"], ["buyer@example.test"])
            self.assertIn("/SYNTHETIC-MATCH", sender.messages[0]["html"])
            self.assertEqual((await connection.execute(select(
                EmailLog.search_id, EmailLog.listing_id, EmailLog.message_id))).all(),
                [(1, "SYNTHETIC-MATCH", "fake-1")])
            await run_worker(sessions, sender)
            await connection.execute(update(SavedSearch).values(last_alert_sent=datetime(2026, 1, 1)))
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.messages), 1)

    async def test_map_square_footage_and_acreage_filters(self):
        from sqlalchemy import update
        from app.models import SavedSearch
        cases = [({"minSqft": 1801}, 0), ({"maxSqft": 1799}, 0),
                 ({"minAcres": 0.26}, 0), ({"maxAcres": 0.24}, 0),
                 ({"minSqft": 1800, "maxSqft": 1800, "minAcres": .25, "maxAcres": .25}, 1)]
        for extra, expected in cases:
            with self.subTest(criteria=extra):
                async with alert_database() as (connection, sessions):
                    criteria = {"cities": ["White Salmon"], "maxPrice": 700000,
                                "minBeds": 3, **extra}
                    await connection.execute(update(SavedSearch).values(criteria=criteria))
                    sender = FakeEmailSender()
                    await run_worker(sessions, sender)
                    self.assertEqual(len(sender.messages), expected)

    async def test_property_missing_id_preserves_baseline_and_retries(self):
        await self.check_property_retry(FakeEmailSender(reject_first=True))

    async def test_property_exception_preserves_baseline_and_retries(self):
        await self.check_property_retry(FakeEmailSender(raise_first=True))

    async def check_property_retry(self, sender):
        from sqlalchemy import select, update
        from app.models import SavedSearch, Listing, EmailLog
        async with alert_database() as (connection, sessions):
            criteria = {"alert_type": "property", "mls_number": "SYNTHETIC-MATCH",
                        "last_price": 600000, "last_status": "Pending"}
            await connection.execute(update(SavedSearch).values(criteria=criteria))
            await run_worker(sessions, sender)
            self.assertEqual((await connection.execute(select(SavedSearch.criteria))).scalar_one(), criteria)
            self.assertEqual((await connection.execute(select(EmailLog.id))).all(), [])
            await run_worker(sessions, sender)
            saved = (await connection.execute(select(SavedSearch.criteria))).scalar_one()
            self.assertEqual((saved["last_price"], saved["last_status"]), (550000, "Active"))
            self.assertEqual(len(sender.messages), 1)
            self.assertIn("Price Change", sender.messages[0]["subject"])
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.attempts), 2)
            # A second change on the same MLS must remain alertable.
            await connection.execute(update(Listing).where(Listing.mls_number == "SYNTHETIC-MATCH").values(price=525000))
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.messages), 2)

    async def test_property_initial_baseline_and_publication_policy(self):
        from sqlalchemy import select, update
        from app.models import SavedSearch, Listing
        async with alert_database() as (connection, sessions):
            await connection.execute(update(SavedSearch).values(criteria={
                "alert_type": "property", "mls_number": "SYNTHETIC-MATCH"}))
            sender = FakeEmailSender()
            await run_worker(sessions, sender)
            criteria = (await connection.execute(select(SavedSearch.criteria))).scalar_one()
            self.assertEqual(criteria["last_price"], 550000)
            self.assertEqual(sender.attempts, [])
            await connection.execute(update(Listing).where(Listing.mls_number == "SYNTHETIC-MATCH").values(
                is_published=False, status="Off-Market"))
            await run_worker(sessions, sender)
            self.assertEqual(sender.attempts, [])

    async def test_market_seven_day_schedule_retry_and_status_dates(self):
        from sqlalchemy import select, update
        from app.models import SavedSearch, Listing, EmailLog
        async with alert_database() as (connection, sessions):
            baseline = datetime(2026, 1, 1, 13)
            due = baseline + timedelta(days=7)
            await connection.execute(update(SavedSearch).values(criteria={"alert_type": "market", "city": "White Salmon"}))
            # Old pending status with a recent sync is not a new pending transition.
            await connection.execute(update(Listing).values(last_updated=due, status_date=baseline - timedelta(days=1)))
            sender = FakeEmailSender(reject_first=True)
            await run_worker(sessions, sender, at=due - timedelta(seconds=1))
            self.assertEqual(sender.attempts, [])
            await run_worker(sessions, sender, at=due)
            self.assertEqual(len(sender.attempts), 1)
            self.assertEqual((await connection.execute(select(SavedSearch.last_alert_sent))).scalar_one(), baseline)
            self.assertEqual((await connection.execute(select(EmailLog.id))).all(), [])
            await run_worker(sessions, sender, at=due)
            self.assertEqual(len(sender.messages), 1)
            self.assertIn("<strong>0</strong> currently pending", sender.messages[0]["html"])
            await run_worker(sessions, sender, at=due + timedelta(days=6))
            self.assertEqual(len(sender.attempts), 2)
            await connection.execute(update(Listing).where(Listing.status == "Pending").values(status_date=due + timedelta(days=6)))
            await run_worker(sessions, sender, at=due + timedelta(days=7))
            self.assertEqual(len(sender.messages), 2)
            self.assertIn("<strong>1</strong> currently pending", sender.messages[1]["html"])

    async def test_recipient_filter_and_test_mode_safeguards(self):
        from app.services.alert_worker import process_alerts, send_with_resend
        async with alert_database() as (_, sessions):
            sender = FakeEmailSender()
            await run_worker(sessions, sender, test_lead_email="other@example.test")
            self.assertEqual(sender.attempts, [])
            with self.assertRaises(RuntimeError):
                await process_alerts()
            with self.assertRaises(RuntimeError):
                send_with_resend({})


class NetworkGuardTests(unittest.TestCase):
    def test_blocks_external_resolution_and_connections(self):
        with database_only_network("postgresql+asyncpg://localhost:55432/listing_alerts_test") as blocked:
            with self.assertRaises(AssertionError):
                socket.getaddrinfo("example.invalid", 443)
            with socket.socket() as sock, self.assertRaises(AssertionError):
                sock.connect(("127.0.0.1", 443))
        self.assertEqual(len(blocked), 2)


class FakeSenderTests(unittest.TestCase):
    def test_first_rejection_is_retryable_and_not_recorded_as_sent(self):
        sender = FakeEmailSender(reject_first=True)
        message = {"to": ["buyer@example.test"], "html": "synthetic listing A"}
        self.assertEqual(sender(message), {})
        self.assertEqual(sender.messages, [])
        self.assertEqual(sender(message), {"id": "fake-1"})
        self.assertEqual(len(sender.attempts), 2)
        self.assertEqual(sender.messages, [message])
