"""Admin authentication and private reporting, using only synthetic local data."""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

from test_alert_integration import alert_database

PASSWORD = "synthetic-password-only"
ORIGIN = "https://admin.example.test"


@asynccontextmanager
async def admin_client(sessions=None, configured=True, production=False):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.admin_auth import _attempts, hash_password
    from app.routers.admin import router, admin_db, private_headers
    app = FastAPI()
    app.include_router(router)
    app.middleware("http")(private_headers)
    db = AsyncMock()
    async def database():
        if sessions is None:
            yield db
        else:
            async with sessions() as session:
                yield session
    app.dependency_overrides[admin_db] = database
    _attempts.clear()
    env = {"ADMIN_USERNAME": "owner", "ADMIN_PASSWORD_HASH": hash_password(PASSWORD),
           "ADMIN_SESSION_SECRET": "synthetic-test-session-secret-at-least-32-characters",
           "ADMIN_ALLOWED_ORIGINS": ORIGIN}
    if production:
        env["APP_ENV"] = "production"
    if not configured:
        env["ADMIN_SESSION_SECRET"] = ""
    with patch.dict(os.environ, env):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN,
                               headers={"Origin": ORIGIN}) as client:
            yield client, db
    _attempts.clear()


async def sign_in(client):
    response = await client.post("/api/admin/login", json={"username": "owner", "password": PASSWORD})
    if response.status_code != 200:
        raise AssertionError("Synthetic admin login failed")
    return response


class AdminAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_reports_reject_anonymous_requests_before_database_access(self):
        async with admin_client() as (client, db):
            for path in ("/activity", "/clients", "/clients/1", "/clients/1/searches", "/clients/1/emails",
                         "/clients/1/emails/1", "/clients/1/emails/1/events"):
                response = await client.get("/api/admin" + path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["cache-control"], "no-store")
            db.execute.assert_not_called()
            db.get.assert_not_called()

    async def test_login_secure_cookie_csrf_logout_and_expiry(self):
        async with admin_client(production=True) as (client, _):
            response = await sign_in(client)
            cookie = response.headers["set-cookie"]
            for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/admin"):
                self.assertIn(flag, cookie)
            session = await client.get("/api/admin/session")
            self.assertEqual(session.json()["username"], "owner")
            csrf = session.json()["csrf_token"]
            response = await client.post("/api/admin/logout")
            self.assertEqual(response.status_code, 403)
            response = await client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf})
            self.assertEqual(response.status_code, 200)
            self.assertEqual((await client.get("/api/admin/session")).status_code, 401)
            await sign_in(client)
            with patch("app.admin_auth.time.time", return_value=10**12):
                self.assertEqual((await client.get("/api/admin/session")).status_code, 401)

    async def test_wrong_password_bad_origin_tampered_cookie_and_rotation(self):
        async with admin_client() as (client, _):
            response = await client.post("/api/admin/login", json={"username": "owner", "password": "wrong"})
            self.assertEqual(response.status_code, 401)
            response = await client.post("/api/admin/login", json={"username": "owner", "password": PASSWORD},
                                         headers={"Origin": "https://untrusted.example.test"})
            self.assertEqual(response.status_code, 403)
            await sign_in(client)
            from app.admin_auth import COOKIE
            token = client.cookies.get(COOKIE)
            client.cookies.clear()
            response = await client.get("/api/admin/session", headers={"Cookie": f"{COOKIE}={token}x"})
            self.assertEqual(response.status_code, 401)
            await sign_in(client)
            with patch.dict(os.environ, {"ADMIN_SESSION_SECRET": "rotated-test-secret-at-least-32-characters"}):
                self.assertEqual((await client.get("/api/admin/session")).status_code, 401)

    async def test_signin_rate_limit_and_missing_configuration(self):
        async with admin_client() as (client, _):
            for _ in range(10):
                await client.post("/api/admin/login", json={"username": "owner", "password": "wrong"})
            self.assertEqual((await client.post("/api/admin/login", json={"username": "owner", "password": PASSWORD})).status_code, 429)
        async with admin_client(configured=False) as (client, db):
            self.assertEqual((await client.get("/api/admin/clients")).status_code, 503)
            db.execute.assert_not_called()

    async def test_reporting_database_errors_are_private_and_retryable(self):
        from sqlalchemy.exc import SQLAlchemyError
        async with admin_client() as (client, db):
            await sign_in(client)
            db.execute.side_effect = SQLAlchemyError("synthetic-sensitive-database-detail")
            with self.assertLogs("app.routers.admin", level="ERROR"):
                response = await client.get("/api/admin/clients")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("synthetic-sensitive", response.text)


@unittest.skipUnless(os.getenv("RUN_ALERT_DB_TESTS") == "1", "opt-in local database test")
class AdminReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_search_email_and_timeline_are_correctly_associated(self):
        from sqlalchemy import insert
        from app.models import Lead, SavedSearch, EmailLog
        from app.services.email_events import record_event
        async with alert_database() as (connection, sessions):
            await connection.execute(insert(Lead), [{"id": 2, "email": "other@example.test", "name": "Other Client"}])
            await connection.execute(insert(SavedSearch), [{"id": 2, "lead_id": 2, "criteria": {}, "is_active": False}])
            async with sessions() as db:
                log = EmailLog(search_id=1, listing_id="SYNTHETIC-MATCH", user_email="buyer@example.test",
                               message_id="admin-message", open_count=12, click_count=4)
                baseline = EmailLog(search_id=1, listing_id="BASELINE", message_id="BACKFILL_DRY_RUN")
                other = EmailLog(search_id=2, listing_id="OTHER", message_id="other-message")
                db.add_all([log, baseline, other])
                await db.commit()
                log_id, other_id = log.id, other.id
                for event_id, kind, hour, details in (
                    ("admin-delivery", "email.delivered", 9, {}),
                    ("admin-open", "email.opened", 10, {}),
                    ("admin-click", "email.clicked", 11, {"link": "https://gorgerealty.com/property/test/SYNTHETIC-MATCH"}),
                    ("admin-sent-late", "email.sent", 8, {}),
                ):
                    values = {"provider_event_id": event_id, "message_id": "admin-message", "event_type": kind,
                              "occurred_at": datetime(2026, 1, 3, hour, tzinfo=timezone.utc), "details": details,
                              "processing_status": "pending"}
                    await record_event(db, values)
                    await record_event(db, values)
                await db.commit()
            async with admin_client(sessions) as (client, _):
                await sign_in(client)
                response = await client.get("/api/admin/clients?q=buyer&page_size=1")
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data["total"], 1)
                self.assertEqual(data["items"][0]["email_count"], 1)
                self.assertEqual(data["items"][0]["recorded_open_count"], 1)
                self.assertNotIn("unsubscribe_token", response.text)
                self.assertEqual((await client.get("/api/admin/clients?q=%25")).json()["total"], 0)
                self.assertEqual((await client.get("/api/admin/clients?page=0")).status_code, 422)
                self.assertEqual((await client.get("/api/admin/clients?page_size=101")).status_code, 422)
                self.assertEqual((await client.get("/api/admin/clients/999")).status_code, 404)
                response = await client.get("/api/admin/clients/1/searches")
                self.assertEqual(response.json()["items"][0]["criteria"]["minBeds"], 3)
                response = await client.get("/api/admin/clients/1/emails")
                self.assertEqual(response.json()["total"], 1)
                row = response.json()["items"][0]
                self.assertEqual(row["delivery_status"], "delivered")
                self.assertEqual((row["recorded_open_count"], row["recorded_click_count"]), (1, 1))
                self.assertGreater(row["legacy_open_count"], 1)
                self.assertEqual(row["listing_address"], "1 Fictional Test Lane")
                self.assertTrue(row["accepted_at"].endswith("Z") or row["accepted_at"].endswith("+00:00"))
                self.assertEqual((await client.get(f"/api/admin/clients/1/emails/{other_id}")).status_code, 404)
                response = await client.get(f"/api/admin/clients/1/emails/{log_id}/events?page_size=2")
                self.assertEqual(response.json()["total"], 4)
                self.assertEqual(len(response.json()["items"]), 2)
                self.assertTrue(response.json()["items"][0]["is_listing_click"])
                self.assertNotIn("other-message", response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("realty_admin", response.text)

    async def test_empty_and_paused_client_with_paginated_directory(self):
        from sqlalchemy import insert, update
        from app.models import Lead, SavedSearch
        async with alert_database() as (connection, sessions):
            await connection.execute(insert(Lead), [{"id": 2, "email": "paused@example.test",
                                                   "name": "Paused Buyer", "is_unsubscribed": True}])
            await connection.execute(update(SavedSearch).values(is_active=False))
            async with admin_client(sessions) as (client, _):
                await sign_in(client)
                response = await client.get("/api/admin/clients?page_size=1")
                self.assertEqual((response.json()["total"], response.json()["pages"]), (2, 2))
                first = response.json()["items"][0]["id"]
                second = (await client.get("/api/admin/clients?page_size=1&page=2")).json()["items"][0]["id"]
                self.assertNotEqual(first, second)
                row = (await client.get("/api/admin/clients/2")).json()
                self.assertTrue(row["is_unsubscribed"])
                self.assertEqual((row["email_count"], row["active_search_count"]), (0, 0))
                self.assertEqual((await client.get("/api/admin/clients/2/emails")).json()["items"], [])

    async def test_activity_dashboard_orders_clients_and_filters_verified_events(self):
        from datetime import timedelta
        from sqlalchemy import insert
        from app.models import Lead, SavedSearch, EmailLog
        from app.services.email_events import record_event
        now = datetime.now(timezone.utc)
        async with alert_database() as (connection, sessions):
            await connection.execute(insert(Lead), [
                {'id': 2, 'email': 'recent@example.test', 'name': 'Recent Buyer', 'is_unsubscribed': True},
                {'id': 3, 'email': 'quiet@example.test', 'name': 'Quiet Buyer', 'is_unsubscribed': False},
                {'id': 4, 'email': 'older@example.test', 'name': 'Older Buyer', 'is_unsubscribed': False},
            ])
            await connection.execute(insert(SavedSearch), [
                {'id': i, 'lead_id': i, 'criteria': {}, 'is_active': True} for i in (2, 3, 4)
            ])
            async with sessions() as db:
                logs = [EmailLog(search_id=i, listing_id='SYNTHETIC-MATCH',
                                 message_id=f'activity-{i}', user_email='synthetic@example.test',
                                 open_count=100 if i == 3 else 0) for i in (1, 2, 3, 4)]
                logs.append(EmailLog(search_id=1, listing_id='BASELINE', message_id='BACKFILL_DRY_RUN'))
                db.add_all(logs)
                await db.commit()
                cases = [
                    ('activity-1', 'email.opened', now - timedelta(days=2), {}),
                    ('activity-1', 'email.clicked', now - timedelta(hours=3), {'link': 'https://gorgerealty.com/unsubscribe?token=synthetic'}),
                    ('activity-1', 'email.clicked', now - timedelta(hours=2), {'link': 'https://gorgerealty.com/property/test/SYNTHETIC-MATCH'}),
                    ('activity-2', 'email.opened', now - timedelta(hours=1), {}),
                    ('activity-3', 'email.delivered', now - timedelta(minutes=5), {}),
                    ('activity-4', 'email.opened', now - timedelta(days=90), {}),
                    ('BACKFILL_DRY_RUN', 'email.opened', now - timedelta(minutes=1), {}),
                    ('unassociated', 'email.opened', now - timedelta(minutes=1), {}),
                ]
                for index, (message, kind, occurred, details) in enumerate(cases):
                    event = {'provider_event_id': f'activity-event-{index}', 'message_id': message,
                             'event_type': kind, 'occurred_at': occurred, 'details': details,
                             'processing_status': 'pending'}
                    await record_event(db, event)
                    await record_event(db, event)
                await db.commit()
            async with admin_client(sessions) as (client, _):
                await sign_in(client)
                response = await client.get('/api/admin/activity')
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual([row['id'] for row in data['items']], [2, 1])
                self.assertEqual(data['summary'], {'active_clients': 2, 'recorded_opens': 2, 'recorded_clicks': 2})
                self.assertTrue(data['items'][0]['is_unsubscribed'])
                self.assertEqual(data['items'][0]['last_activity_kind'], 'email_open')
                self.assertEqual(data['items'][1]['last_activity_kind'], 'listing_click')
                self.assertEqual(data['items'][1]['last_email_id'], logs[0].id)
                self.assertEqual(data['items'][1]['listing_address'], '1 Fictional Test Lane')
                opened = (await client.get('/api/admin/clients/1/emails?opened_only=true&days=30')).json()
                self.assertEqual(opened['total'], 1)
                self.assertEqual(opened['items'][0]['id'], logs[0].id)
                self.assertEqual(opened['items'][0]['period_open_count'], 1)
                self.assertIsNotNone(opened['items'][0]['last_recorded_open_at'])
                self.assertEqual((await client.get('/api/admin/clients/1/emails?opened_only=true&days=1')).json()['total'], 0)
                self.assertEqual((await client.get('/api/admin/clients/4/emails?opened_only=true&days=30')).json()['total'], 0)
                self.assertEqual((await client.get('/api/admin/clients/4/emails?opened_only=true&days=0')).json()['total'], 1)
                all_time = (await client.get('/api/admin/activity?days=0')).json()
                self.assertEqual([row['id'] for row in all_time['items']], [2, 1, 4])
                self.assertEqual((await client.get('/api/admin/activity?days=1')).json()['summary']['recorded_opens'], 1)
                one_page = (await client.get('/api/admin/activity?page_size=1')).json()
                self.assertEqual((one_page['total'], one_page['pages']), (2, 2))
                self.assertEqual(one_page['summary']['recorded_clicks'], 2)
                self.assertEqual((await client.get('/api/admin/activity?page_size=1&page=2')).json()['items'][0]['id'], 1)
                self.assertEqual((await client.get('/api/admin/activity?days=-1')).status_code, 422)
                self.assertEqual((await client.get('/api/admin/activity?days=366')).status_code, 422)
                self.assertEqual(response.headers['cache-control'], 'no-store')
