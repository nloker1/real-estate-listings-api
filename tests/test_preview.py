"""Preview safety tests; only optional DB test contacts guarded local PostgreSQL."""
import os
from pathlib import Path
import tempfile
import unittest
import io
import stat
from contextlib import redirect_stdout
from unittest.mock import patch, Mock
from uuid import uuid4

from test_alert_integration import database_only_network


class PreviewConfigurationTests(unittest.TestCase):
    def test_setup_writes_private_config_without_printing_credentials(self):
        from scripts.configure_preview import main
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'preview.env'
            output = io.StringIO()
            with patch('scripts.configure_preview.PREVIEW_FILE', target), \
                 patch('scripts.configure_preview.dotenv_values', return_value={
                     'DATABASE_URL': 'postgresql+asyncpg://synthetic:fake-db-secret@db.example.invalid/listings',
                     'RESEND_API_KEY': 'fake-provider-key'}), \
                 patch('scripts.configure_preview.getpass.getpass', return_value='synthetic-preview-password'), \
                 patch('sys.argv', ['configure_preview', '--tunnel', '--ssh-host', 'root@server.example.invalid']), \
                 redirect_stdout(output):
                main()
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            settings = dotenv_values(target, interpolate=False)
            self.assertIn('@127.0.0.1:55433/', settings['PREVIEW_DATABASE_URL'])
            self.assertNotIn('RESEND_API_KEY', settings)
            self.assertNotIn('fake-db-secret', output.getvalue())
            self.assertNotIn('synthetic-preview-password', output.getvalue())
            self.assertNotIn(settings['ADMIN_SESSION_SECRET'], output.getvalue())
            self.assertIn('root@server.example.invalid', output.getvalue())

    def test_explicit_url_required_without_production_fallback(self):
        from app.preview import preview_database_url
        with patch.dict(os.environ, {'APP_ENV': 'preview', 'DATABASE_URL': 'production-placeholder'}, clear=True), \
             patch('app.preview.PREVIEW_FILE', Path('/nonexistent-preview-config')):
            with self.assertRaisesRegex(ValueError, 'PREVIEW_DATABASE_URL'):
                preview_database_url()

    def test_preview_file_whitelist_and_provider_credentials_removed(self):
        from app.preview import preview_database_url
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'preview.env'
            url='postgresql+asyncpg://synthetic@localhost:55432/listing_alerts_test'
            path.write_text(f"PREVIEW_DATABASE_URL='{url}'\nADMIN_USERNAME=local-owner\n"
                            "ADMIN_PASSWORD_HASH='scrypt$test$test'\nADMIN_SESSION_SECRET=synthetic\n"
                            "RESEND_API_KEY=should-not-load\nAPP_ENV=production\n")
            with patch.dict(os.environ, {'APP_ENV':'preview','RESEND_API_KEY':'remove-me','RMLS_TOKEN':'remove-me'}, clear=True), \
                 patch('app.preview.PREVIEW_FILE',path):
                self.assertEqual(preview_database_url(),url)
                self.assertEqual(os.environ['APP_ENV'],'preview')
                self.assertEqual(os.environ['ADMIN_USERNAME'],'local-owner')
                self.assertNotIn('RESEND_API_KEY',os.environ)
                self.assertNotIn('RMLS_TOKEN',os.environ)
                self.assertIn('http://localhost:3001',os.environ['ADMIN_ALLOWED_ORIGINS'])

    def test_connection_is_readonly_and_remote_tls_is_verified(self):
        from app.preview import connection_options
        local=connection_options('postgresql+asyncpg://localhost:55432/listing_alerts_test')
        self.assertEqual(local['server_settings']['default_transaction_read_only'],'on')
        self.assertNotIn('ssl',local)
        remote=connection_options('postgresql+asyncpg://database.example.invalid/preview')
        self.assertTrue(remote['ssl'].check_hostname)
        self.assertEqual(remote['timeout'],10)


class PreviewHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_mutations_and_nonlocal_access_blocked_before_handlers(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.preview import preview_guard
        app=FastAPI()
        called=Mock()
        @app.api_route('/{path:path}',methods=['GET','POST','DELETE'])
        async def route(path):
            called(path)
            return {'ok':True}
        app.middleware('http')(preview_guard)
        with patch.dict(os.environ, {'APP_ENV':'preview'}):
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://localhost:8000',
                                   headers={'Origin':'http://localhost:3001'}) as client:
                for method,path in [('POST','/api/saved-searches'),('POST','/api/v1/webhooks/resend'),
                                    ('GET','/api/alerts/unsubscribe?token=synthetic'),('DELETE','/api/admin/clients/1')]:
                    self.assertEqual((await client.request(method,path)).status_code,403)
                called.assert_not_called()
                self.assertEqual((await client.get('/api/listings')).status_code,200)
                self.assertEqual((await client.get('/api/admin/activity')).status_code,200)
                self.assertEqual((await client.post('/api/admin/login')).status_code,200)
                response=await client.get('/api/admin/session')
                self.assertEqual(response.headers['x-data-mode'],'production-readonly-preview')
                self.assertEqual((await client.get('/api/listings',headers={'Origin':'https://untrusted.example'})).status_code,403)
            async with AsyncClient(transport=ASGITransport(app=app,client=('192.0.2.1',1234)),
                                   base_url='http://localhost:8000') as client:
                self.assertEqual((await client.get('/api/listings')).status_code,403)

    async def test_worker_and_sender_disabled_in_preview(self):
        from app.services.alert_worker import process_alerts, send_with_resend
        sender=Mock()
        with patch.dict(os.environ,{'APP_ENV':'preview'}):
            with self.assertRaisesRegex(RuntimeError,'disabled'):
                send_with_resend({})
            with self.assertRaisesRegex(RuntimeError,'disabled'):
                await process_alerts(session_factory=Mock(),sender=sender)
            sender.assert_not_called()


@unittest.skipUnless(os.getenv('RUN_ALERT_DB_TESTS')=='1','opt-in local database test')
class PreviewDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgresql_enforces_readonly_transactions(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.exc import DBAPIError
        from app.database_config import test_database_url
        from app.preview import connection_options
        self.assertEqual(os.getenv('APP_ENV'),'test')
        url=test_database_url(os.environ)
        with database_only_network(url):
            engine=create_async_engine(url,connect_args=connection_options(url))
            try:
                async with engine.connect() as connection:
                    self.assertEqual((await connection.execute(text('SHOW transaction_read_only'))).scalar_one(),'on')
                    self.assertEqual((await connection.execute(text('SELECT 1'))).scalar_one(),1)
                    with self.assertRaises(DBAPIError) as caught:
                        await connection.execute(text('CREATE TABLE preview_guard_'+uuid4().hex+' (id integer)'))
                    self.assertEqual(caught.exception.orig.sqlstate,'25006')
                    await connection.rollback()
            finally:
                await engine.dispose()
