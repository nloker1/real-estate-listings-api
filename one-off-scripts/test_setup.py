import asyncio
import os
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from app.database import AsyncSessionLocal
from app.models import SavedSearch, Listing, Lead, EmailLog

async def setup_test():
    """
    Forces a match in the database so the Alert Worker has something to send.
    """
    # Use the email you signed up with in the UI
    TEST_EMAIL = "nloker1@gmail.com" 
    
    async with AsyncSessionLocal() as db:
        # 1. Find your lead
        lead_result = await db.execute(select(Lead).where(Lead.email == TEST_EMAIL))
        lead = lead_result.scalars().first()
        
        if not lead:
            print(f"❌ Error: Lead {TEST_EMAIL} not found. Sign up on the website first!")
            return

        # 2. Find your search and "Reset" it
        search_result = await db.execute(select(SavedSearch).where(SavedSearch.lead_id == lead.id))
        search = search_result.scalars().first()
        
        if not search:
            print(f"❌ Error: No saved search found for {TEST_EMAIL}.")
            return

        # Set search back in time so it's "due" for an update
        search.last_alert_sent = datetime.utcnow() - timedelta(days=7)
        print(f"✅ Reset search timers for {TEST_EMAIL}")

        # 3. Find ONE active listing and force it to match your search
        listing_result = await db.execute(select(Listing).where(Listing.status == 'Active').limit(1))
        listing = listing_result.scalars().first()
        
        if listing:
            # Force the listing to look brand new
            listing.created_at = datetime.utcnow()
            
            # --- THE MAGIC PART ---
            # We update the listing to match your specific criteria exactly 
            # so the query in alert_worker.py doesn't skip it.
            criteria = search.criteria
            if criteria.get('minPrice'):
                listing.price = int(criteria['minPrice']) + 1000
            if criteria.get('minBeds'):
                listing.beds = int(criteria['minBeds'])
            
            print(f"✅ Modified Listing {listing.mls_number} (${listing.price}) to match your criteria.")

            # 4. Clear any previous logs so the duplicate check doesn't block us
            await db.execute(delete(EmailLog).where(EmailLog.user_email == TEST_EMAIL))
            print(f"✅ Cleared existing email logs for {TEST_EMAIL} to allow re-sending.")

        await db.commit()
        print("\n🚀 TEST READY: Now run: ./venv/bin/python3 app/services/alert_worker.py")

if __name__ == "__main__":
    # Ensure we can import app modules
    import sys
    sys.path.append(os.getcwd())
    asyncio.run(setup_test())
