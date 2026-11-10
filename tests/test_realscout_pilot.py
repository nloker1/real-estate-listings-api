"""Pilot migration tests: synthetic source records, isolated local database only."""
import os
import unittest
from sqlalchemy import select, text, update, func
from scripts.import_realscout_contacts import Contact, ImportProblem
from scripts.import_realscout_pilot import PilotManifest, import_pilot
from test_alert_integration import alert_database


def manifest():
    return PilotManifest.model_validate({'version': 1, 'contacts': ['pilot-1', 'pilot-2'], 'searches': [
        {'contact_id': 'pilot-1', 'search_id': 'alert-1', 'criteria': {'zipcodes': ['98672'], 'minBeds': 3}, 'frequency': 'monthly'},
        {'contact_id': 'pilot-2', 'search_id': 'alert-2', 'criteria': {'zipcodes': ['97031'], 'maxPrice': 700000}, 'frequency': 'daily'},
    ]})


def contacts():
    return [Contact(2, 'pilot-1', 'buyer@example.com', 'Incoming name', None, False, 1, 0, 0),
            Contact(3, 'pilot-2', 'second@example.com', 'Synthetic Second', None, True, 1, 0, 0)]


class PilotValidationTests(unittest.TestCase):
    def test_limits_and_blocked_filters(self):
        from pydantic import ValidationError
        from app.routers.admin_searches import Criteria
        for value in ({'zipcodes': ['invalid']}, {'zipcodes': ['9867']}, {'zipcodes': ['98672', '97031'], 'maxPrice': -1}):
            with self.assertRaises(ValidationError): Criteria.model_validate(value)
        self.assertEqual(Criteria(zipcodes=['98672', '98672']).zipcodes, ['98672'])
        base = manifest().model_dump()
        for changes in ({'contacts': ['a', 'b', 'c', 'd']}, {'contacts': ['a']},
                        {'searches': [{**base['searches'][0], 'blocked_reasons': ['unmapped geometry']}]},
                        {'searches': [{**base['searches'][0], 'review_required': False}]}):
            with self.assertRaises(ValidationError): PilotManifest.model_validate({**base, **changes})


@unittest.skipUnless(os.getenv('RUN_ALERT_DB_TESTS') == '1', 'opt-in local database test')
class PilotDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_paused_import_repeat_and_unsubscribe_preservation(self):
        from app.models import Lead, SavedSearch, RealScoutContactImport, EmailLog
        async with alert_database() as (connection, sessions):
            schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
            await connection.execute(text(f'ALTER SEQUENCE "{schema}".leads_id_seq RESTART WITH 100'))
            await connection.execute(text(f'ALTER SEQUENCE "{schema}".saved_searches_id_seq RESTART WITH 100'))
            await connection.execute(update(Lead).where(Lead.id == 1).values(email='buyer@example.com', is_unsubscribed=True))
            async with sessions() as db:
                dry = await import_pilot(db, contacts(), manifest())
                self.assertEqual(dry['new_clients'], 1)
                self.assertEqual(dry['new_paused_searches'], 2)
                self.assertFalse(db.new)
            async with sessions() as db:
                report = await import_pilot(db, contacts(), manifest(), True)
                await db.commit()
                self.assertEqual(report['new_paused_searches'], 2)
                imported = (await db.execute(select(SavedSearch).where(SavedSearch.id >= 100))).scalars().all()
                self.assertEqual(len(imported), 2)
                self.assertTrue(all(not s.is_active and s.last_alert_sent is None for s in imported))
                self.assertEqual({s.frequency for s in imported}, {'daily', 'monthly'})
                self.assertTrue(all(s.criteria['_realscout']['review_required'] for s in imported))
                self.assertTrue((await db.get(Lead, 1)).is_unsubscribed)
                # Source deleted flag has no effect on the new client's unsubscribe flag.
                second = (await db.execute(select(Lead).where(Lead.email == 'second@example.com'))).scalar_one()
                self.assertFalse(second.is_unsubscribed)
                again = await import_pilot(db, contacts(), manifest(), True)
                await db.commit()
                self.assertEqual(again['new_clients'], 0)
                self.assertEqual(again['new_paused_searches'], 0)
                self.assertEqual(again['reused_paused_searches'], 2)
                self.assertEqual((await db.execute(select(func.count()).select_from(RealScoutContactImport))).scalar_one(), 2)
                self.assertEqual((await db.execute(select(func.count()).select_from(EmailLog))).scalar_one(), 0)
                imported[0].is_active = True
                await db.commit()
            with self.assertRaises(ImportProblem):
                async with sessions() as db:
                    async with db.begin():
                        await import_pilot(db, contacts(), manifest(), True)
            self.assertTrue((await connection.execute(select(SavedSearch.is_active).where(SavedSearch.id == 100))).scalar_one())

    async def test_source_collision_rolls_back_contacts_and_zip_matching_is_exact(self):
        from app.models import Lead, SavedSearch, Listing, RealScoutContactImport
        from app.services.search_matching import listing_matches
        async with alert_database() as (connection, sessions):
            schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
            await connection.execute(text(f'ALTER SEQUENCE "{schema}".leads_id_seq RESTART WITH 100'))
            await connection.execute(update(SavedSearch).where(SavedSearch.id == 1).values(criteria={'_realscout': {'search_id': 'alert-2'}}))
            # Matching ignores city text when only ZIP is specified; it still enforces active/published.
            await connection.execute(update(Listing).where(Listing.mls_number == 'SYNTHETIC-MATCH').values(city='Different City'))
            async with sessions() as db:
                matches = (await db.execute(listing_matches({'zipcodes': ['98672'], 'maxPrice': 700000, 'minBeds': 3}))).scalars().all()
                self.assertEqual([r.mls_number for r in matches], ['SYNTHETIC-MATCH'])
                self.assertEqual((await db.execute(listing_matches({'zipcodes': ['99999']}))).scalars().all(), [])
            with self.assertRaises(ImportProblem):
                async with sessions() as db:
                    async with db.begin():
                        await import_pilot(db, contacts(), manifest(), True)
            self.assertEqual((await connection.execute(select(func.count()).select_from(Lead))).scalar_one(), 1)
            self.assertEqual((await connection.execute(select(func.count()).select_from(RealScoutContactImport))).scalar_one(), 0)
