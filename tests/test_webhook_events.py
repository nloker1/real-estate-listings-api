"""Signed synthetic webhooks: ASGI in process, fake clients, no provider requests."""
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from test_alert_integration import alert_database

SIGNING_KEY = b"synthetic-webhook-signing-key-only"
SECRET = "whsec_" + base64.b64encode(SIGNING_KEY).decode()
URL = "/api/v1/webhooks/resend"


def event_body(kind="email.opened", at="2026-01-03T12:00:00Z", message="fake-1", **data):
    return json.dumps({"type": kind, "created_at": at,
                       "data": {"email_id": message, "to": ["buyer@example.test"], **data}},
                      ensure_ascii=False).encode()


def signed_headers(body, event_id="evt-synthetic-1", timestamp=None):
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    signature = base64.b64encode(hmac.new(
        SIGNING_KEY, event_id.encode() + b"." + stamp.encode() + b"." + body,
        hashlib.sha256).digest()).decode()
    return {"svix-id": event_id, "svix-timestamp": stamp,
            "svix-signature": "v1," + signature, "content-type": "application/json"}


@asynccontextmanager
async def webhook_client(sessions):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.routers.webhooks import router, webhook_db
    app = FastAPI()
    app.include_router(router)
    async def database():
        async with sessions() as db:
            yield db
    app.dependency_overrides[webhook_db] = database
    with patch.dict(os.environ, {"RESEND_WEBHOOK_SECRET": SECRET}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


class WebhookSignatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMock()
        @asynccontextmanager
        async def sessions():
            yield self.db
        self.sessions = sessions

    async def test_invalid_missing_stale_future_and_tampered_signatures(self):
        body = event_body()
        cases = [(body, {}), (body + b" ", signed_headers(body)),
                 (body, signed_headers(body, timestamp=int(time.time()) - 3600)),
                 (body, signed_headers(body, timestamp=int(time.time()) + 3600))]
        async with webhook_client(self.sessions) as client:
            for content, headers in cases:
                with self.subTest(headers=list(headers)):
                    response = await client.post(URL, content=content, headers=headers)
                    self.assertEqual(response.status_code, 400)
        self.db.execute.assert_not_called()
        self.db.commit.assert_not_called()

    async def test_missing_secret_fails_closed(self):
        async with webhook_client(self.sessions) as client:
            with patch.dict(os.environ, {}, clear=True):
                response = await client.post(URL, content=event_body())
                self.assertEqual(response.status_code, 503)
        self.db.execute.assert_not_called()

    async def test_valid_raw_utf8_and_bounded_payload_validation(self):
        from app.services.email_events import verify_event
        body = event_body("email.clicked", click={"link": "https://example.test/listing/é"})
        values = verify_event(body, signed_headers(body), SECRET)
        self.assertEqual(values["details"]["engagement_source"], "unclassified")
        self.assertEqual(values["occurred_at"], datetime(2026, 1, 3, 12, tzinfo=timezone.utc))
        self.assertNotIn("to", values)
        async with webhook_client(self.sessions) as client:
            malformed = [b"[]", b"{", event_body(at="2026-01-01"),
                         event_body("email.clicked", click=None), event_body(message=None)]
            for body in malformed:
                response = await client.post(URL, content=body, headers=signed_headers(body))
                self.assertEqual(response.status_code, 400)
            body = b"x" * (256 * 1024 + 1)
            response = await client.post(URL, content=body, headers=signed_headers(body))
            self.assertEqual(response.status_code, 413)
        self.db.execute.assert_not_called()

    async def test_verified_unsupported_event_is_ignored(self):
        body = event_body("domain.created")
        async with webhook_client(self.sessions) as client:
            response = await client.post(URL, content=body, headers=signed_headers(body))
            self.assertEqual(response.json(), {"status": "ignored"})
        self.db.execute.assert_not_called()


@unittest.skipUnless(os.getenv("RUN_ALERT_DB_TESTS") == "1", "opt-in local database test")
class WebhookDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def add_log(self, sessions, message="fake-1"):
        from app.models import EmailLog
        async with sessions() as db:
            log = EmailLog(search_id=1, listing_id="SYNTHETIC-MATCH",
                           user_email="buyer@example.test", message_id=message)
            db.add(log)
            await db.commit()
            return log.id

    async def post(self, client, kind, event_id, at="2026-01-03T12:00:00Z", **data):
        body = event_body(kind, at, **data)
        response = await client.post(URL, content=body, headers=signed_headers(body, event_id))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_duplicate_and_out_of_order_events_use_provider_time(self):
        from sqlalchemy import select, func
        from app.models import EmailEvent, EmailLog
        async with alert_database() as (connection, sessions):
            log_id = await self.add_log(sessions)
            async with webhook_client(sessions) as client:
                await self.post(client, "email.opened", "open-new", "2026-01-03T12:00:00Z")
                duplicate = await self.post(client, "email.opened", "open-new")
                self.assertEqual(duplicate["status"], "duplicate")
                # Even a signed duplicate with changed content cannot redirect
                # the original event to another message or count it twice.
                await self.post(client, "email.opened", "open-new", message="another-email")
                await self.post(client, "email.opened", "open-old", "2026-01-03T10:00:00Z")
                await self.post(client, "email.clicked", "click-new", click={"link": "https://example.test/new"})
                await self.post(client, "email.clicked", "click-old", "2026-01-03T09:00:00Z",
                                click={"link": "https://example.test/old"})
            async with sessions() as db:
                log = await db.get(EmailLog, log_id)
                self.assertEqual((log.open_count, log.click_count), (2, 2))
                self.assertEqual(log.opened_at.hour, 10)
                self.assertEqual(log.clicked_at.hour, 9)
                self.assertEqual(log.last_clicked_url, "https://example.test/new")
                self.assertEqual((await db.execute(select(func.count(EmailEvent.id)))).scalar_one(), 4)
                events = (await db.execute(select(EmailEvent))).scalars().all()
                self.assertTrue(all(e.email_log_id == log_id and e.processing_status == "processed" for e in events))
                self.assertTrue(all(e.received_at.tzinfo is not None for e in events))

    async def test_early_events_reconcile_after_message_is_logged(self):
        from sqlalchemy import select
        from app.models import EmailEvent, EmailLog
        from app.services.reconcile_email_events import reconcile
        async with alert_database() as (_, sessions):
            async with webhook_client(sessions) as client:
                result = await self.post(client, "email.opened", "early-open")
                self.assertEqual(result["applied"], 0)
                async with sessions() as db:
                    event = (await db.execute(select(EmailEvent))).scalar_one()
                    self.assertEqual(event.processing_status, "pending")
                    self.assertIsNone(event.email_log_id)
                log_id = await self.add_log(sessions)
                self.assertEqual(await reconcile(sessions), 1)
                self.assertEqual(await reconcile(sessions), 0)
                await self.post(client, "email.opened", "early-open")
                async with sessions() as db:
                    self.assertEqual((await db.get(EmailLog, log_id)).open_count, 1)

    async def test_ambiguous_message_and_unrelated_events_remain_pending(self):
        from sqlalchemy import select
        from app.models import EmailEvent, EmailLog
        from app.services.email_events import reconcile_pending
        async with alert_database() as (_, sessions):
            first = await self.add_log(sessions)
            second = await self.add_log(sessions)
            async with webhook_client(sessions) as client:
                await self.post(client, "email.opened", "ambiguous")
                await self.post(client, "email.opened", "unknown", message="another-email")
            async with sessions() as db:
                for log_id in (first, second):
                    self.assertEqual((await db.get(EmailLog, log_id)).open_count, 0)
                events = (await db.execute(select(EmailEvent))).scalars().all()
                self.assertEqual(len(events), 2)
                self.assertTrue(all(e.processing_status == "pending" for e in events))
                self.assertEqual(await reconcile_pending(db, limit=1), 0)

    async def test_delivery_bounce_and_complaint_are_distinct_from_engagement(self):
        from sqlalchemy import select
        from app.models import EmailEvent, EmailLog
        async with alert_database() as (_, sessions):
            log_id = await self.add_log(sessions)
            async with webhook_client(sessions) as client:
                for kind in ("email.sent", "email.delivered", "email.delivery_delayed",
                             "email.bounced", "email.complained", "email.failed", "email.suppressed"):
                    await self.post(client, kind, kind)
            async with sessions() as db:
                log = await db.get(EmailLog, log_id)
                self.assertEqual((log.open_count, log.click_count), (0, 0))
                events = (await db.execute(select(EmailEvent))).scalars().all()
                self.assertEqual(len(events), 7)
                self.assertTrue(all(e.email_log_id == log_id for e in events))

    async def test_failed_commit_returns_retryable_error_and_rolls_back(self):
        from sqlalchemy import select, func
        from sqlalchemy.exc import SQLAlchemyError
        from app.models import EmailEvent, EmailLog
        async with alert_database() as (connection, sessions):
            log_id = await self.add_log(sessions)
            @asynccontextmanager
            async def broken_commit_sessions():
                async with sessions() as db:
                    with patch.object(db, "commit", new=AsyncMock(side_effect=SQLAlchemyError("synthetic"))):
                        yield db
            body = event_body()
            async with webhook_client(broken_commit_sessions) as client:
                with self.assertLogs("app.routers.webhooks", level="ERROR"):
                    response = await client.post(URL, content=body, headers=signed_headers(body))
                self.assertEqual(response.status_code, 503)
            self.assertEqual((await connection.execute(select(func.count(EmailEvent.id)))).scalar_one(), 0)
            async with webhook_client(sessions) as client:
                await self.post(client, "email.opened", "evt-synthetic-1")
            async with sessions() as db:
                self.assertEqual((await db.get(EmailLog, log_id)).open_count, 1)
