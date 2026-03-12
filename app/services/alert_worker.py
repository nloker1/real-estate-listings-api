import os
import asyncio
import resend
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.database import AsyncSessionLocal
from app.models import SavedSearch, Listing, EmailLog, Lead

resend.api_key = os.getenv("RESEND_API_KEY")

async def process_alerts():
    print("Checking for Saved Search Matches...")
    async with AsyncSessionLocal() as db:
        
        # FIX 3: The JOIN. We get the Search AND the Lead in one single query.
        stmt = (
            select(SavedSearch)
            .options(joinedload(SavedSearch.lead)) # Assumes you have a relationship set up
            .where(SavedSearch.is_active == True)
        )
        result = await db.execute(stmt)
        searches = result.scalars().all()
        
        print(f"Found {len(searches)} active searches.")

        for search in searches:
            # We already have the lead from the JOIN! No extra query needed.
            lead = search.lead 
            
            if not lead or not lead.email:
                print(f"Skipping search {search.id}: No lead email.")
                continue

            # Ensure we are using a naive datetime for comparison (matching the DB column)
            since_time = search.last_alert_sent or search.created_at
            if since_time and since_time.tzinfo is not None:
                since_time = since_time.replace(tzinfo=None)

            criteria = search.criteria
            query = select(Listing).where(Listing.is_published == True)
            query = query.where(Listing.created_at > since_time)
            
            # ... (rest of query building)

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
            
            if criteria.get('cities'):
                normalized_cities = [c.replace(" ", "") for c in criteria['cities']]
                from sqlalchemy import or_
                city_filters = [Listing.city.ilike(f"%{c}%") for c in normalized_cities]
                query = query.where(or_(*city_filters))

            match_result = await db.execute(query)
            new_listings = match_result.scalars().all()

            if not new_listings:
                continue

            # FIX 1: Get all previously emailed listings for this search in ONE query
            log_stmt = select(EmailLog.listing_id).where(EmailLog.search_id == search.id)
            log_result = await db.execute(log_stmt)
            emailed_listing_ids = set(log_result.scalars().all()) # Put them in a fast Python set

            for listing in new_listings:
                # Now we just check the Python set. Zero database hits inside this loop!
                if listing.mls_number in emailed_listing_ids:
                    continue

                subject = f"New Match: {listing.address} - ${listing.price:,}"
                html_content = f"""
                <div style="font-family: sans-serif; max-width: 600px; border: 1px solid #eee; padding: 20px;">
                    <h2 style="color: #1a5091;">A new home matches your search!</h2>
                    <img src="{listing.photo_url}" style="width: 100%; border-radius: 8px;" />
                    <h3 style="margin-top: 15px;">{listing.address}</h3>
                    <p style="font-size: 18px; font-weight: bold;">${listing.price:,}</p>
                    <p>{listing.beds} Beds | {listing.baths} Baths | {listing.sqft:,} SqFt</p>
                    <a href="https://gorgerealty.com/property/{listing.mls_number}" 
                       style="display: inline-block; background: #1a5091; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 10px;">
                       View Full Listing Details
                    </a>
                </div>
                """

                try:
                    params = {
                        "from": "Gorge Realty Alerts <alerts@gorgerealty.com>",
                        "to": [lead.email],
                        "subject": subject,
                        "html": html_content,
                    }
                    email_response = resend.Emails.send(params)

                    new_log = EmailLog(
                        search_id=search.id,
                        listing_id=listing.mls_number,
                        user_email=lead.email,
                        message_id=email_response.get("id")
                    )
                    db.add(new_log)
                    
                    # Update last alert time (using naive UTC to match DB schema)
                    search.last_alert_sent = datetime.utcnow()
                    
                    print(f"Sent alert for {listing.mls_number} to {lead.email}")

                except Exception as e:
                    print(f"Resend error: {str(e)}")

        # Commit all the new email logs and updated search timestamps at once
        await db.commit()

if __name__ == "__main__":
    asyncio.run(process_alerts())