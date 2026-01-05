import asyncio
import os
from app.database import AsyncSessionLocal, engine # Changed to AsyncSessionLocal
from app import models
from app.engine import process_alerts

async def run_test():
    # Use the Async session maker
    async with AsyncSessionLocal() as db:
        print("--- Starting Async Alert Test ---")

        try:
            test_email = "nloker1@gmail.com" # CHANGE THIS
            
            # 1. Clean up old data (Async style)
            from sqlalchemy import delete
            await db.execute(delete(models.EmailLog).where(models.EmailLog.user_email == test_email))
            await db.execute(delete(models.SavedSearch).where(models.SavedSearch.user_email == test_email))
            
            # 2. Setup: Create test search
            search = models.SavedSearch(
                user_email=test_email,
                city="Hood River",
                max_price=900000,
                is_active=True
            )
            db.add(search)
            
            # 3. Setup: Create matching listing
            listing = models.Listing(
                mls_number="MLS-12345",
                address="1234 Belmont Ave",
                city="Hood River",
                price=850000,
                is_new=True
            )
            db.add(listing)
            await db.commit()
            print(f"Created Test Search and Listing.")

            # 4. Execution: Run the Engine
            # Note: You might need to make process_alerts an 'async def' in engine.py
            print("Running Match Engine...")
            await process_alerts(db)
            
            print("Test complete. Check your inbox and database!")

        except Exception as e:
            print(f"ERROR: {e}")
            await db.rollback()

if __name__ == "__main__":
    asyncio.run(run_test())