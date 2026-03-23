import os
import asyncio
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.database import AsyncSessionLocal
from app.models import SavedSearch, Listing, EmailLog

async def backfill_logs():
    """
    Dry run script to populate EmailLog for all current matches.
    This 'marks as read' existing listings so they won't trigger 
    emails on the first cron run.
    """
    print("🚀 Starting DRY RUN (Backfill)...")
    async with AsyncSessionLocal() as db:
        
        # Get all active searches
        stmt = (
            select(SavedSearch)
            .options(joinedload(SavedSearch.lead))
            .where(SavedSearch.is_active == True)
        )
        result = await db.execute(stmt)
        searches = result.scalars().all()
        
        print(f"Found {len(searches)} active searches.")

        total_backfilled = 0

        for search in searches:
            lead = search.lead 
            if not lead:
                continue

            # We use a broad time check for backfill - anything since search creation
            since_time = search.created_at
            if since_time and since_time.tzinfo is not None:
                since_time = since_time.replace(tzinfo=None)

            criteria = search.criteria
            print(f"  - Processing Search {search.id} (Lead: {lead.email})")

            query = select(Listing).where(Listing.is_published == True)
            query = query.where(Listing.status == 'Active')
            
            # Match current filters
            if criteria.get('minPrice'):
                query = query.where(Listing.price >= int(criteria['minPrice']))
            if criteria.get('maxPrice'):
                query = query.where(Listing.price <= int(criteria['maxPrice']))
            if criteria.get('minBeds'):
                query = query.where(Listing.beds >= int(criteria['minBeds']))
            if criteria.get('minBaths'):
                query = query.where(Listing.baths >= float(criteria['minBaths']))
            if criteria.get('propertyType'):
                query = query.where(Listing.property_type == criteria['propertyType'])
            
            match_result = await db.execute(query)
            pre_city_listings = match_result.scalars().all()
            print(f"  - Initial matches (price/status): {len(pre_city_listings)}")

            # --- ADDING CITY FILTER LOGIC (Matching alert_worker.py) ---
            if criteria.get('cities'):
                from sqlalchemy import or_, func
                city_filters = []
                for c in criteria['cities']:
                    # 1. Match as-is (e.g. "Hood River")
                    city_filters.append(Listing.city.ilike(f"%{c}%"))
                    # 2. Match without spaces (e.g. "HoodRiver")
                    no_spaces = c.replace(' ', '')
                    city_filters.append(Listing.city.ilike(f"%{no_spaces}%"))
                    # 3. Handle DB-side no-space comparison
                    city_filters.append(func.replace(Listing.city, ' ', '').ilike(f"%{no_spaces}%"))
                
                query = query.where(or_(*city_filters))

            match_result = await db.execute(query)
            all_matches = match_result.scalars().all()
            print(f"  - Final matches (after City filter): {len(all_matches)}")

            # Get existing logs to avoid duplicates even in backfill
            log_stmt = select(EmailLog.listing_id).where(EmailLog.search_id == search.id)
            log_result = await db.execute(log_stmt)
            emailed_listing_ids = set(log_result.scalars().all())

            for listing in all_matches:
                if str(listing.mls_number) in emailed_listing_ids:
                    continue

                # CREATE LOG WITHOUT SENDING EMAIL
                new_log = EmailLog(
                    search_id=search.id,
                    listing_id=str(listing.mls_number),
                    user_email=lead.email,
                    message_id="BACKFILL_DRY_RUN"
                )
                db.add(new_log)
                
                # Update last alert time to now so future runs are truly incremental
                search.last_alert_sent = datetime.utcnow()
                total_backfilled += 1
                
            await db.commit()
            print(f"    - Backfilled {len(all_matches)} listings for this search.")

    print(f"\n✅ Finished! Backfilled {total_backfilled} logs. No emails were sent.")

if __name__ == "__main__":
    asyncio.run(backfill_logs())
