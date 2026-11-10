"""CSV checks are offline; optional imports use disposable local synthetic data."""
import csv
from dataclasses import replace
from datetime import datetime
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sqlalchemy import select, update, func, text
from scripts.import_realscout_contacts import Contact, load_contacts, offline_report, main, import_contacts
from test_alert_integration import alert_database

HEADERS = ['Contact ID', 'Contact Email', 'First Name', 'Last Name', 'Deleted?',
           'Listing Alert Count', 'Market Alert Count', 'Home Value Alert Count', 'Primary Home Phone']


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=HEADERS)
        writer.writeheader()
        for changes in rows:
            writer.writerow({**{'Contact ID': 'source-1', 'Contact Email': 'fictional@example.com',
                'First Name': 'Private Fictional', 'Last Name': 'Buyer', 'Deleted?': 'False',
                'Listing Alert Count': '1', 'Market Alert Count': '0', 'Home Value Alert Count': '0',
                'Primary Home Phone': ''}, **changes})


class CSVImportTests(unittest.TestCase):
    def test_default_is_offline_and_deleted_is_metadata_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.csv'
            write_csv(path, [{'Deleted?': 'True'}])
            output = io.StringIO()
            with patch('scripts.import_realscout_contacts.database_report') as database, redirect_stdout(output):
                self.assertEqual(main([str(path)]), 0)
            database.assert_not_called()
            report = json.loads(output.getvalue())
            self.assertEqual(report['source_marked_deleted'], 1)
            self.assertFalse(report['applied'])
            self.assertNotIn('fictional@example.com', output.getvalue())
            self.assertNotIn('Private Fictional', output.getvalue())
            self.assertNotIn('source-1', output.getvalue())

    def test_duplicate_or_invalid_rows_block_entire_apply(self):
        cases = [[{}, {'Contact ID': 'source-2', 'Contact Email': 'FICTIONAL@EXAMPLE.COM'}],
                 [{'Contact Email': 'not-an-email'}], [{'Deleted?': ''}],
                 [{'Listing Alert Count': '-1'}], [{}, {'Contact Email': 'other@example.com'}]]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.csv'
            for rows in cases:
                write_csv(path, rows)
                with patch('scripts.import_realscout_contacts.database_report') as database, redirect_stdout(io.StringIO()):
                    self.assertEqual(main([str(path), '--apply']), 1)
                database.assert_not_called()

    def test_private_report_refuses_overwrite_and_flags_bad_phone(self):
        with tempfile.TemporaryDirectory() as folder:
            path, report = Path(folder) / 'input.csv', Path(folder) / 'report.json'
            write_csv(path, [{'Primary Home Phone': 'invalid-private-phone'}])
            contacts, issues = load_contacts(path)
            self.assertEqual(offline_report(contacts, issues)['errors'], 0)
            self.assertIsNone(contacts[0].phone)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(path), '--report', str(report)]), 0)
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            original = report.read_text()
            with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()):
                self.assertEqual(main([str(path), '--report', str(report)]), 1)
            self.assertEqual(report.read_text(), original)

    def test_preview_blocks_apply_before_database_access(self):
        from unittest.mock import AsyncMock
        import asyncio
        from scripts.import_realscout_contacts import database_report, ImportProblem
        with patch.dict(os.environ, {'APP_ENV': 'preview'}):
            with self.assertRaises(ImportProblem):
                asyncio.run(database_report([], True))
            db = AsyncMock()
            with self.assertRaises(ImportProblem):
                asyncio.run(import_contacts(db, [], True))
            db.execute.assert_not_called()


@unittest.skipUnless(os.getenv('RUN_ALERT_DB_TESTS') == '1', 'opt-in local database test')
class RealScoutDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_apply_repeat_preferences_phone_conflicts_and_source_identity(self):
        from app.models import Lead, RealScoutContactImport, SavedSearch, EmailLog
        async with alert_database() as (connection, sessions):
            schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
            await connection.execute(text(f'ALTER SEQUENCE "{schema}".leads_id_seq RESTART WITH 100'))
            await connection.execute(update(Lead).where(Lead.id == 1).values(email='Buyer@Example.com'))
            old_date = datetime(2026, 1, 1)
            async with sessions() as db:
                db.add(Lead(id=2, email='keep@example.com', name='Keep existing name',
                    phone='(541) 555-0100', is_unsubscribed=True, unsubscribed_at=old_date))
                await db.commit()
            contacts = [
                Contact(2, 'source-1', 'buyer@example.com', 'Replacement name', None, True, 2, 0, 0),
                Contact(3, 'source-2', 'keep@example.com', 'Replacement name', None, False, 1, 0, 0),
                Contact(4, 'source-3', 'active@example.com', 'Active Buyer', '+15415550101', False, 1, 0, 0),
                Contact(5, 'source-4', 'deleted@example.com', 'Deleted Buyer', '+15415550101', True, 1, 0, 0),
                Contact(6, 'source-5', 'shared@example.com', 'Shared Phone', '+15415550100', False, 0, 0, 1),
            ]
            async with sessions() as db:
                dry = await import_contacts(db, contacts)
                self.assertEqual((dry['new_clients'], dry['existing_clients'], dry['phones_omitted']), (3, 2, 3))
                self.assertFalse(db.new)
                self.assertFalse(db.dirty)
            self.assertEqual((await connection.execute(select(func.count()).select_from(Lead))).scalar_one(), 2)
            async with sessions() as db:
                report = await import_contacts(db, contacts, True)
                self.assertFalse(report['conflicts'])
                await db.commit()
            async with sessions() as db:
                leads = (await db.execute(select(Lead).order_by(Lead.id))).scalars().all()
                self.assertEqual(len(leads), 5)
                by_email = {lead.email.lower(): lead for lead in leads}
                self.assertFalse(by_email['buyer@example.com'].is_unsubscribed)
                self.assertIsNone(by_email['buyer@example.com'].unsubscribed_at)
                self.assertEqual(by_email['buyer@example.com'].name, 'Synthetic Buyer')
                self.assertTrue(by_email['keep@example.com'].is_unsubscribed)
                self.assertEqual(by_email['keep@example.com'].unsubscribed_at, old_date)
                self.assertFalse(by_email['active@example.com'].is_unsubscribed)
                self.assertFalse(by_email['deleted@example.com'].is_unsubscribed)
                self.assertIsNone(by_email['deleted@example.com'].unsubscribed_at)
                self.assertTrue(all(lead.unsubscribe_token for lead in leads))
                self.assertTrue(all(by_email[email].phone is None for email in ['active@example.com', 'deleted@example.com', 'shared@example.com']))
                mappings = (await db.execute(select(RealScoutContactImport))).scalars().all()
                self.assertEqual(len(mappings), 5)
                self.assertTrue(next(m for m in mappings if m.contact_id == 'source-4').source_deleted)
                original_dates = {lead.id: lead.unsubscribed_at for lead in leads}
                again = await import_contacts(db, [replace(c, deleted=not c.deleted) for c in contacts], True)
                await db.commit()
                self.assertFalse(by_email['buyer@example.com'].is_unsubscribed)
                self.assertTrue(by_email['keep@example.com'].is_unsubscribed)
                self.assertFalse(by_email['deleted@example.com'].is_unsubscribed)
                self.assertEqual(again['new_clients'], 0)
                self.assertEqual(again['new_source_mappings'], 0)
                self.assertEqual({lead.id: lead.unsubscribed_at for lead in leads}, original_dates)
                self.assertEqual((await db.execute(select(func.count()).select_from(SavedSearch))).scalar_one(), 1)
                self.assertEqual((await db.execute(select(func.count()).select_from(EmailLog))).scalar_one(), 0)
                # Changed source-ID/email association blocks the whole batch.
                conflicted = [replace(contacts[0], email='changed@example.com'),
                              Contact(7, 'source-6', 'extra@example.com', '', None, False, 0, 0, 0)]
                blocked = await import_contacts(db, conflicted, True)
                self.assertTrue(blocked['conflicts'])
                self.assertFalse(db.new)
                self.assertEqual((await db.execute(select(func.count()).select_from(Lead))).scalar_one(), 5)

    async def test_mid_import_error_rolls_back_all_changes(self):
        from app.models import Lead, RealScoutContactImport
        from app.services.unsubscribe import ensure_unsubscribe_token
        async with alert_database() as (connection, sessions):
            schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
            await connection.execute(text(f'ALTER SEQUENCE "{schema}".leads_id_seq RESTART WITH 100'))
            await connection.execute(update(Lead).where(Lead.id == 1).values(email='buyer@example.com', name=None))
            contacts = [Contact(2, 'source-1', 'buyer@example.com', 'Temporary name', None, True, 1, 0, 0),
                        Contact(3, 'source-2', 'second@example.com', 'Second client', None, False, 1, 0, 0)]
            calls = 0
            async def fail_second(db, lead):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError('synthetic failure after earlier rows flushed')
                return await ensure_unsubscribe_token(db, lead)
            with self.assertRaises(RuntimeError):
                async with sessions() as db:
                    async with db.begin():
                        with patch('app.services.unsubscribe.ensure_unsubscribe_token', side_effect=fail_second):
                            await import_contacts(db, contacts, True)
            self.assertEqual(calls, 2)
            self.assertFalse((await connection.execute(select(Lead.is_unsubscribed).where(Lead.id == 1))).scalar_one())
            self.assertIsNone((await connection.execute(select(Lead.name).where(Lead.id == 1))).scalar_one())
            self.assertEqual((await connection.execute(select(func.count()).select_from(Lead))).scalar_one(), 1)
            self.assertEqual((await connection.execute(select(func.count()).select_from(RealScoutContactImport))).scalar_one(), 0)

    async def test_ambiguous_existing_email_is_not_silently_merged(self):
        from app.models import Lead
        async with alert_database() as (connection, sessions):
            await connection.execute(update(Lead).where(Lead.id == 1).values(email='buyer@example.com'))
            async with sessions() as db:
                db.add(Lead(id=2, email='BUYER@example.com'))
                await db.commit()
                contacts = [Contact(2, 'source-1', 'buyer@example.com', '', None, False, 0, 0, 0)]
                report = await import_contacts(db, contacts, True)
                self.assertEqual(len(report['conflicts']), 1)
                self.assertFalse(db.new)
