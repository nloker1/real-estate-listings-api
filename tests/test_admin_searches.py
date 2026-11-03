"""Admin editor tests use the guarded disposable DB and synthetic records only."""
import os
import unittest
from unittest.mock import patch
from sqlalchemy import select, text
from test_admin import admin_client, sign_in
from test_alert_integration import alert_database

BODY = {'criteria': {'cities': ['White Salmon'], 'minPrice': 400000, 'maxPrice': 700000, 'minBeds': 3}}


class SearchAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_require_auth_origin_csrf_and_writable_mode(self):
        async with admin_client() as (client, db):
            self.assertEqual((await client.post('/api/admin/clients/1/searches', json=BODY)).status_code, 401)
            result = await sign_in(client)
            headers = {'X-CSRF-Token': result.json()['csrf_token']}
            self.assertEqual((await client.post('/api/admin/clients/1/searches', json=BODY)).status_code, 403)
            self.assertEqual((await client.post('/api/admin/clients/1/searches', json=BODY,
                headers={**headers, 'Origin': 'https://foreign.example.test'})).status_code, 403)
            with patch.dict(os.environ, {'APP_ENV': 'preview'}):
                self.assertEqual((await client.post('/api/admin/clients/1/searches', json=BODY, headers=headers)).status_code, 403)
            db.execute.assert_not_called()
            db.get.assert_not_called()

    async def test_rejects_invalid_filters_without_database_access(self):
        async with admin_client() as (client, db):
            result = await sign_in(client)
            headers = {'X-CSRF-Token': result.json()['csrf_token']}
            for filters in ({'minPrice': 100, 'maxPrice': 50}, {'minBeds': -1}, {'minSqft': 1.5},
                            {'cities': ['%']}, {'cities': ['   ']}, {'unknown': True}, {'minAcres': 'NaN'}):
                response = await client.post('/api/admin/clients/1/search-preview', json={'criteria': filters}, headers=headers)
                self.assertEqual(response.status_code, 422)
            db.execute.assert_not_called()
            db.get.assert_not_called()


@unittest.skipUnless(os.getenv('RUN_ALERT_DB_TESTS') == '1', 'opt-in local database test')
class SearchEditorTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_preview_edit_pause_activation_and_ownership(self):
        from app.models import Lead, SavedSearch
        async with alert_database() as (connection, sessions):
            schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
            await connection.execute(text(f'ALTER SEQUENCE "{schema}".saved_searches_id_seq RESTART WITH 100'))
            async with admin_client(sessions) as (client, _):
                headers = {'X-CSRF-Token': (await sign_in(client)).json()['csrf_token']}
                preview = await client.post('/api/admin/clients/1/search-preview', json=BODY, headers=headers)
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual([row['mls_number'] for row in preview.json()['items']], ['SYNTHETIC-MATCH'])
                self.assertIn('after search creation', preview.json()['note'])
                zero = await client.post('/api/admin/clients/1/search-preview', json={'criteria': {'maxPrice': 0}}, headers=headers)
                self.assertEqual(zero.json()['total'], 0)
                second = await client.post('/api/admin/clients/1/search-preview?page=2&page_size=1', json=BODY, headers=headers)
                self.assertEqual(second.json()['items'], [])
                self.assertEqual(second.json()['total'], 1)
                with patch.dict(os.environ, {'APP_ENV': 'preview'}):
                    self.assertEqual((await client.post('/api/admin/clients/1/search-preview', json=BODY, headers=headers)).status_code, 200)
                created = await client.post('/api/admin/clients/1/searches', json=BODY, headers=headers)
                self.assertEqual(created.status_code, 201, created.text)
                search_id = created.json()['id']
                self.assertFalse(created.json()['is_active'])
                base = f'/api/admin/clients/1/searches/{search_id}'
                response = await client.patch(base + '/state', json={'is_active': True}, headers=headers)
                self.assertTrue(response.json()['is_active'])
                changed = {'criteria': {**BODY['criteria'], 'maxPrice': 600000}}
                response = await client.put(base, json=changed, headers=headers)
                self.assertFalse(response.json()['is_active'])
                async with sessions() as db:
                    search = await db.get(SavedSearch, search_id)
                    created_at = search.created_at
                    self.assertEqual(search.criteria['maxPrice'], 600000)
                    lead = await db.get(Lead, 1)
                    lead.is_unsubscribed = True
                    await db.commit()
                self.assertEqual((await client.patch(base + '/state', json={'is_active': True}, headers=headers)).status_code, 409)
                self.assertEqual((await client.patch(base + '/state', json={'is_active': False}, headers=headers)).status_code, 200)
                wrong = f'/api/admin/clients/999/searches/{search_id}'
                self.assertEqual((await client.put(wrong, json=BODY, headers=headers)).status_code, 404)
                async with sessions() as db:
                    search = await db.get(SavedSearch, search_id)
                    self.assertEqual(search.created_at, created_at)
                    search.criteria = {'alert_type': 'property', 'mls_number': 'SYNTHETIC-MATCH'}
                    await db.commit()
                self.assertEqual((await client.put(base, json=BODY, headers=headers)).status_code, 409)
                self.assertEqual((await client.post('/api/admin/clients/999/searches', json=BODY, headers=headers)).status_code, 404)
                self.assertEqual((await client.post('/api/admin/clients/999/search-preview', json=BODY, headers=headers)).status_code, 404)
                # Preview never creates email history or changes subscription state.
                from app.models import EmailLog
                self.assertEqual((await connection.execute(select(EmailLog))).all(), [])
                self.assertTrue((await connection.execute(select(Lead.is_unsubscribed).where(Lead.id == 1))).scalar_one())
