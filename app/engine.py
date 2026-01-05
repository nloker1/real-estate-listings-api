import resend
import os
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from . import models

resend.api_key = os.getenv("RESEND_API_KEY")

async def process_alerts(db: AsyncSession): # Changed to async def
    """
    The Match Engine: Finds new matches and logs them using Async logic.
    """
    # 1. Get all active searches (Async Syntax)
    result = await db.execute(select(models.SavedSearch).filter(models.SavedSearch.is_active == True))
    active_searches = result.scalars().all()

    for search in active_searches:
        # 2. Find matching listings that haven't been sent yet
        # We build a subquery to find what has already been sent
        already_sent_query = select(models.EmailLog.listing_id).filter(models.EmailLog.user_email == search.user_email)
        
        # Build the main matching query
        query = select(models.Listing).filter(
            models.Listing.city == search.city,
            models.Listing.price >= (search.min_price or 0),
            models.Listing.price <= (search.max_price or 99999999),
            ~models.Listing.mls_number.in_(already_sent_query)
        )
        
        listing_result = await db.execute(query)
        new_matches = listing_result.scalars().all()

        for listing in new_matches:
            # 3. Send the email (Resend's SDK is sync, but we call it inside our async loop)
            resend_response = send_alert_email(search.user_email, listing)
            
            # 4. Record the event in the Log
            new_log = models.EmailLog(
                search_id=search.id,
                listing_id=listing.mls_number,
                user_email=search.user_email,
                message_id=resend_response.get('id')
            )
            db.add(new_log)
            
        # Commit once per search to save progress
        await db.commit()

def send_alert_email(email, listing):
    """
    Helper to format the HTML and send via Resend.
    """
    return resend.Emails.send({
        "from": "Gorge Property Search <alerts@lokerrealty.com>",
        "to": email,
        "subject": f"New Match in {listing.city}: ${listing.price:,}",
        "html": f"""
            <div style="font-family: sans-serif; max-width: 600px; border: 1px solid #eee; padding: 20px;">
                <h2 style="color: #2c3e50;">New Property Match!</h2>
                <p>A property matching your search just hit the market:</p>
                <div style="background: #f9f9f9; padding: 15px; border-radius: 8px;">
                    <strong style="font-size: 18px;">{listing.address}</strong><br>
                    <span style="color: #27ae60; font-weight: bold; font-size: 20px;">${listing.price:,}</span>
                    <p>{listing.beds} Bed | {listing.baths} Bath | {listing.sqft or 'N/A'} SqFt</p>
                </div>
                <br>
                <a href="https://yourdomain.com/map?mls={listing.mls_number}" 
                   style="background: #2980b9; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; display: inline-block;">
                   View on Map
                </a>
                <hr style="margin-top: 30px; border: 0; border-top: 1px solid #eee;">
                <p style="font-size: 12px; color: #7f8c8d;">
                    Real Broker LLC | Nate Loker <br>
                    Licensed in OR & WA
                </p>
            </div>
        """
    })