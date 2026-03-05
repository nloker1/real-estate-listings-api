import os
import asyncio
import resend
from datetime import datetime, timezone
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.models import SavedSearch, Listing, EmailLog, Lead

# 1. Setup Resend
resend.api_key = os.getenv("RESEND_API_KEY")

async def process_alerts():
    """
    The main worker function that:
    1. Finds all active saved searches.
    2. Checks for NEW listings that match the search criteria.
    3. Sends an email for each match.
    """
    async with AsyncSessionLocal() as db:
        # Get all active searches and their associated Lead (user)
        stmt = select(SavedSearch).where(SavedSearch.is_active == True)
        result = await db.execute(stmt)
        searches = result.scalars().all()

        for search in searches:
            # Get the user's email
            lead_stmt = select(Lead).where(Lead.id == search.lead_id)
            lead_result = await db.execute(lead_stmt)
            lead = lead_result.scalars().first()
            
            if not lead or not lead.email:
                continue

            # --- MATCHING LOGIC ---
            # We look for listings created/updated AFTER the last time we sent an alert
            # If we've never sent one, we use the search creation time.
            since_time = search.last_alert_sent or search.created_at

            criteria = search.criteria
            query = select(Listing).where(Listing.is_published == True)
            query = query.where(Listing.created_at > since_time)

            # Apply stored JSON criteria to the SQLAlchemy query
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
            
            # City Logic (Handling the multi-city list)
            if criteria.get('cities'):
                normalized_cities = [c.replace(" ", "") for c in criteria['cities']]
                from sqlalchemy import or_
                city_filters = [Listing.city.ilike(f"%{c}%") for c in normalized_cities]
                query = query.where(or_(*city_filters))

            # Run the query to see if there are matches
            match_result = await db.execute(query)
            new_listings = match_result.scalars().all()

            if not new_listings:
                continue

            # --- EMAIL SENDING ---
            for listing in new_listings:
                # Basic email layout
                subject = f"New Match: {listing.address} - ${listing.price:,}"
                
                # Check if we already emailed this user about this specific property
                log_stmt = select(EmailLog).where(
                    and_(
                        EmailLog.user_email == lead.email,
                        EmailLog.listing_id == listing.mls_number
                    )
                )
                existing_log = await db.execute(log_stmt)
                if existing_log.scalars().first():
                    continue

                html_content = f"""
                <div style="font-family: sans-serif; max-width: 600px; border: 1px solid #eee; padding: 20px;">
                    <h2 style="color: #1a5091;">A new home matches your search!</h2>
                    <img src="{listing.photo_url}" style="width: 100%; border-radius: 8px;" />
                    <h3 style="margin-top: 15px;">{listing.address}</h3>
                    <p style="font-size: 18px; font-weight: bold;">${listing.price:,}</p>
                    <p>{listing.beds} Beds | {listing.baths} Baths | {listing.sqft:,} SqFt</p>
                    <p style="color: #666; font-style: italic;">{listing.listing_brokerage}</p>
                    <a href="https://gorgerealty.com/property/{listing.mls_number}" 
                       style="display: inline-block; background: #1a5091; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 10px;">
                       View Full Listing Details
                    </a>
                    <hr style="margin-top: 30px; border: 0; border-top: 1px solid #eee;" />
                    <p style="font-size: 12px; color: #999;">
                        You are receiving this because you created an alert on gorgerealty.com.
                    </p>
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

                    # Log the success
                    new_log = EmailLog(
                        search_id=search.id,
                        listing_id=listing.mls_number,
                        user_email=lead.email,
                        message_id=email_response.get("id")
                    )
                    db.add(new_log)
                    
                    # Update the search timestamp so we don't alert on the same thing again
                    search.last_alert_sent = datetime.utcnow()
                    
                    print(f"Sent alert for {listing.mls_number} to {lead.email}")

                except Exception as e:
                    print(f"Resend error: {str(e)}")

        await db.commit()

if __name__ == "__main__":
    asyncio.run(process_alerts())
