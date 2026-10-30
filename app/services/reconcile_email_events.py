"""Periodic reconciliation of verified events received before their email logs."""
import asyncio
import logging

from app.services.email_events import reconcile_pending


async def reconcile(session_factory):
    async with session_factory() as db:
        applied = await reconcile_pending(db)
        await db.commit()
        return applied


if __name__ == "__main__":
    # No provider calls or email sends. Uses normal environment DB configuration.
    from app.database import AsyncSessionLocal, engine

    async def main():
        try:
            applied = await reconcile(AsyncSessionLocal)
            print(f"Reconciled {applied} email events")
        finally:
            await engine.dispose()

    try:
        asyncio.run(main())
    except Exception:
        logging.error("Email event reconciliation failed; retry required")
        raise SystemExit(1) from None
