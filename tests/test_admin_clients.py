"""Client creation with synthetic data; opt-in DB checks use local test isolation."""
import os
import unittest
from unittest.mock import patch, Mock
from sqlalchemy import select, text, func, update
from test_admin import admin_client, sign_in
from test_alert_integration import alert_database

BODY = {'name': 'Fictional Buyer', 'email': 'fictional.buyer@example.com'}


class AddClientAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_origin_csrf_and_preview_are_required(self):
        async with admin_client() as (client, db):
            self.assertEqual((await client.post('/api/admin/clients', json=BODY)).status_code, 401)
            headers = {'X-CSRF-Token': (await sign_in(client)).json()['csrf_token']}
            self.assertEqual((await client.post('/api/admin/clients', json=BODY)).status_code, 403)
            self.assertEqual((await client.post('/api/admin/clients', json=BODY,
                headers={**headers, 'Origin': 'https://foreign.example.test'})).status_code, 403)
            with patch.dict(os.environ, {'APP_ENV': 'preview'}):
                self.assertEqual((await client.post('/api/admin/clients', json=BODY, headers=headers)).status_code, 403)
            db.execute.assert_not_called()
            db.add.assert_not_called()

    async def test_unique_constraint_failure_rolls_back_without_exposing_database_details(self):
        from sqlalchemy.exc import IntegrityError
        async with admin_client() as (client, db):
            headers = {'X-CSRF-Token': (await sign_in(client)).json()['csrf_token']}
            db.execute.return_value = Mock()
            db.execute.return_value.scalar_one_or_none.return_value = None
            db.add = Mock()
            db.commit.side_effect = IntegrityError('synthetic SQL', {}, Exception('private database details'))
            response = await client.post('/api/admin/clients', json=BODY, headers=headers)
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('private database details', response.text)
            db.rollback.assert_awaited_once()

    async def test_invalid_input_is_rejected_before_database_access(self):
        async with admin_client() as (client, db):
            headers = {'X-CSRF-Token': (await sign_in(client)).json()['csrf_token']}
            for body in ({**BODY, 'name': '   '}, {**BODY, 'email': 'invalid'},
                         {**BODY, 'is_unsubscribed': False}, {**BODY, 'name': 'x' * 201}):
                self.assertEqual((await client.post('/api/admin/clients', json=body, headers=headers)).status_code, 422)
            db.execute.assert_not_called()


@unittest.skipUnless(os.getenv('RUN_ALERT_DB_TESTS') == '1', 'opt-in local database test')
class AddClientDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_client_duplicate_normalization_and_existing_preferences(self):
        from app.models import Lead, SavedSearch, EmailLog
        async with alert_database() as (connection, sessions):
            schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
            await connection.execute(text(f'ALTER SEQUENCE "{schema}".leads_id_seq RESTART WITH 100'))
            await connection.execute(update(Lead).where(Lead.id == 1).values(
                email=' Existing.Buyer@Example.com ', is_unsubscribed=True))
            async with admin_client(sessions) as (client, _):
                headers = {'X-CSRF-Token': (await sign_in(client)).json()['csrf_token']}
                response = await client.post('/api/admin/clients', json={
                    'name': ' Fictional Buyer ', 'email': ' Fictional.Buyer@EXAMPLE.COM '}, headers=headers)
                self.assertEqual(response.status_code, 201, response.text)
                data = response.json()
                self.assertEqual(data['name'], BODY['name'])
                self.assertEqual(data['email'], BODY['email'])
                self.assertNotIn('unsubscribe_token', data)
                async with sessions() as db:
                    lead = await db.get(Lead, data['id'])
                    self.assertTrue(lead.unsubscribe_token)
                # No implicit searches or deliveries are created.
                self.assertEqual((await connection.execute(select(func.count()).select_from(SavedSearch))).scalar_one(), 1)
                self.assertEqual((await connection.execute(select(func.count()).select_from(EmailLog))).scalar_one(), 0)
                for email in ('FICTIONAL.BUYER@example.com', 'existing.buyer@example.com'):
                    duplicate = await client.post('/api/admin/clients', json={**BODY, 'email': email}, headers=headers)
                    self.assertEqual(duplicate.status_code, 409)
                existing = (await connection.execute(select(Lead.name, Lead.is_unsubscribed).where(Lead.id == 1))).one()
                self.assertEqual(existing.name, 'Synthetic Buyer')
                self.assertTrue(existing.is_unsubscribed)
                self.assertEqual((await connection.execute(select(func.count()).select_from(Lead))).scalar_one(), 2)
