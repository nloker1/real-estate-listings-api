"""Repair missing tokens only. Defaults to dry run; never sends email."""
import argparse
import asyncio
from sqlalchemy import select, or_, func
from app.preview import require_writable_mode


async def main(apply=False):
    require_writable_mode()
    from app.database import AsyncSessionLocal
    from app.models import Lead
    from app.services.unsubscribe import ensure_unsubscribe_token
    async with AsyncSessionLocal() as db:
        missing = or_(Lead.unsubscribe_token.is_(None), Lead.unsubscribe_token == '')
        if not apply:
            count = (await db.execute(select(func.count()).select_from(Lead).where(missing))).scalar_one()
            print(f'Dry run: {count} clients need unsubscribe tokens. No changes made.')
            return
        leads = (await db.execute(select(Lead).where(missing))).scalars().all()
        for lead in leads:
            await ensure_unsubscribe_token(db, lead)
        await db.commit()
        print(f'Checked/repaired tokens for {len(leads)} clients. No emails sent; subscription preferences unchanged.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Persist missing tokens in the configured database')
    args = parser.parse_args()
    asyncio.run(main(args.apply))
