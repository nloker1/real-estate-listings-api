"""Synthetic local database only; fake transport and external-network guard."""
import os
import unittest
from sqlalchemy import select, update
from test_alert_integration import alert_database, run_worker
from alert_fakes import FakeEmailSender


@unittest.skipUnless(os.getenv('RUN_ALERT_DB_TESTS') == '1', 'opt-in local database test')
class UnsubscribeTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_repaired_before_send_and_unsubscribe_stops_worker(self):
        from app.models import Lead, EmailLog
        from app.routers.alerts import unsubscribe_lead
        from fastapi import HTTPException
        async with alert_database() as (connection, sessions):
            await connection.execute(update(Lead).where(Lead.id == 1).values(unsubscribe_token=None))
            sender = FakeEmailSender()
            await run_worker(sessions, sender)
            token = (await connection.execute(select(Lead.unsubscribe_token).where(Lead.id == 1))).scalar_one()
            self.assertTrue(token)
            self.assertEqual(len(sender.messages), 1)
            self.assertIn(f'https://gorgerealty.com/unsubscribe?token={token}', sender.messages[0]['html'])
            async with sessions() as db:
                with self.assertRaises(HTTPException) as error:
                    await unsubscribe_lead('invalid-synthetic-token', db)
                self.assertEqual(error.exception.status_code, 404)
                await unsubscribe_lead(token, db)
                lead = await db.get(Lead, 1)
                first = lead.unsubscribed_at
                await unsubscribe_lead(token, db)
                self.assertEqual(lead.unsubscribed_at, first)
                self.assertTrue(lead.is_unsubscribed)
                self.assertEqual(lead.unsubscribe_token, token)
            # Remove send history so suppression cannot be attributed to deduplication.
            from sqlalchemy import delete
            await connection.execute(delete(EmailLog))
            await run_worker(sessions, sender)
            self.assertEqual(len(sender.messages), 1)

    async def test_model_generates_unique_tokens_and_repair_preserves_preferences(self):
        from app.models import Lead
        from app.services.unsubscribe import ensure_unsubscribe_token
        async with alert_database() as (_, sessions):
            async with sessions() as db:
                first = Lead(id=2, email='first@example.test', is_unsubscribed=True)
                second = Lead(id=3, email='second@example.test')
                db.add_all([first, second])
                await db.flush()
                self.assertTrue(first.unsubscribe_token)
                self.assertNotEqual(first.unsubscribe_token, second.unsubscribe_token)
                token = first.unsubscribe_token
                await ensure_unsubscribe_token(db, first)
                self.assertEqual(first.unsubscribe_token, token)
                first.unsubscribe_token = None
                await db.flush()
                await ensure_unsubscribe_token(db, first)
                self.assertTrue(first.unsubscribe_token)
                self.assertTrue(first.is_unsubscribed)
