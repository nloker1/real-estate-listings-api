import os
import asyncio
import resend
import re
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_, func
from sqlalchemy.orm import joinedload
from app.database import AsyncSessionLocal
from app.models import SavedSearch, Listing, EmailLog, Lead

resend.api_key = os.getenv("RESEND_API_KEY")

def create_slug(address):
    """Simple Python version of the frontend slugify logic."""
    if not address:
        return "property"
    # Match frontend: lowercase, spaces to hyphens, remove non-alphanumeric
    slug = address.lower().strip()
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'[^\w\-]+', '', slug)
    slug = re.sub(r'\-\-+', '-', slug)
    return slug

async def send_resend_email(to_email, subject, html_content, db, search, listing_id_str):
    try:
        params = {
            "from": "Gorge Realty Alerts <alerts@gorgerealty.com>",
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        }
        email_response = resend.Emails.send(params)
        
        if email_response and email_response.get("id"):
            new_log = EmailLog(
                search_id=search.id,
                listing_id=listing_id_str,
                user_email=to_email,
                message_id=email_response.get("id")
            )
            db.add(new_log)
            search.last_alert_sent = datetime.utcnow()
            await db.commit()
            print(f"Sent alert to {to_email} (Logged)")
        else:
            print(f"Resend accepted request but returned no ID for {to_email}")

        # RATE LIMIT: Resend allows 2 req/sec. 0.6s delay keeps us safe.
        await asyncio.sleep(0.9)
    except Exception as e:
        await db.rollback()
        error_msg = str(e).lower()
        print(f"Resend error: {str(e)}")
        if "domain" in error_msg or "verify" in error_msg or "unauthorized" in error_msg:
            print("Domain/Auth error detected. Skipping timestamp update for retry.")

async def process_alerts():
    print("Checking for Saved Search Matches...")
    async with AsyncSessionLocal() as db:
        
        stmt = (
            select(SavedSearch)
            .join(Lead)
            .options(joinedload(SavedSearch.lead))
            .where(SavedSearch.is_active == True)
            .where(Lead.is_unsubscribed == False)
        )
        result = await db.execute(stmt)
        searches = result.scalars().all()
        
        print(f"Found {len(searches)} active searches.")

        for search in searches:
            lead = search.lead 
            if not lead or not lead.email:
                continue

            criteria = search.criteria
            alert_type = criteria.get("alert_type")
            
            since_time = search.last_alert_sent or search.created_at
            if since_time and since_time.tzinfo is not None:
                since_time = since_time.replace(tzinfo=None)
                
            unsubscribe_url = f"https://gorgerealty.com/unsubscribe?token={lead.unsubscribe_token}" if lead.unsubscribe_token else "https://gorgerealty.com/contact"

            # ---------------------------------------------------------
            # 1. PROPERTY ALERT
            # ---------------------------------------------------------
            if alert_type == "property":
                mls_number = criteria.get("mls_number")
                if not mls_number:
                    continue
                    
                # Get the current published listing
                q = select(Listing).where(
                    Listing.mls_number == str(mls_number),
                    Listing.is_published == True
                )
                res = await db.execute(q)
                updated_listing = res.scalar_one_or_none()
                
                if updated_listing:
                    last_price = criteria.get("last_price")
                    last_status = criteria.get("last_status")
                    
                    # If we have no baseline, set it now and skip alerting
                    if last_price is None or last_status is None:
                        new_criteria = dict(criteria)
                        new_criteria["last_price"] = updated_listing.price
                        new_criteria["last_status"] = updated_listing.status
                        search.criteria = new_criteria
                        await db.commit()
                        continue
                        
                    price_changed = last_price != updated_listing.price
                    status_changed = last_status != updated_listing.status
                    
                    if price_changed or status_changed:
                        # Update baseline so we don't alert again for this state
                        new_criteria = dict(criteria)
                        new_criteria["last_price"] = updated_listing.price
                        new_criteria["last_status"] = updated_listing.status
                        search.criteria = new_criteria
                        
                        slug = create_slug(updated_listing.address)
                        property_url = f"https://gorgerealty.com/property/{slug}/{updated_listing.mls_number}"
                        
                        updates = []
                        if price_changed:
                            updates.append("Price Change")
                        if status_changed:
                            updates.append(f"Status changed to {updated_listing.status}")
                        update_text = " & ".join(updates)
                        
                        subject = f"Property Update: {updated_listing.address} ({update_text})"
                        html_content = f"""
                        <div style="font-family: sans-serif; max-width: 600px; border: 1px solid #eee; padding: 20px;">
                            <h2 style="color: #1a5091;">An update on the property you are watching</h2>
                            <img src="{updated_listing.photo_url}" style="width: 100%; border-radius: 8px;" />
                            <h3 style="margin-top: 15px;">{updated_listing.address}</h3>
                            <p style="font-size: 18px; font-weight: bold;">${updated_listing.price:,}</p>
                            <p><strong>Status:</strong> {updated_listing.status}</p>
                            <a href="{property_url}" 
                               style="display: inline-block; background: #1a5091; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 10px;">
                               View Full Listing Details
                            </a>
                            <hr style="border: none; border-top: 1px solid #eee; margin-top: 20px;" />
                            <p style="font-size: 12px; color: #999; text-align: center;">
                                Gorge Realty | 123 Main St, Hood River, OR 97031<br />
                                <a href="{unsubscribe_url}" style="color: #999;">Unsubscribe</a> from these alerts
                            </p>
                        </div>
                        """
                        await send_resend_email(lead.email, subject, html_content, db, search, str(mls_number))

            # ---------------------------------------------------------
            # 2. MARKET ALERT
            # ---------------------------------------------------------
            elif alert_type == "market":
                # Only send weekly
                if (datetime.utcnow() - since_time) < timedelta(days=6):
                    continue
                    
                city_name = criteria.get("city")
                if not city_name:
                    continue
                    
                # Grab statistics for that city in the last week to make it interesting
                one_week_ago = datetime.utcnow() - timedelta(days=7)
                
                # 1. New/Updated Active Count
                q_active = select(func.count(Listing.id)).where(
                    Listing.city.ilike(f"%{city_name}%"),
                    Listing.status == 'Active',
                    Listing.last_updated > one_week_ago
                )
                res_active = await db.execute(q_active)
                active_count = res_active.scalar() or 0
                
                # 2. Average & Max Price of those Active Listings
                q_stats = select(
                    func.avg(Listing.price),
                    func.max(Listing.price)
                ).where(
                    Listing.city.ilike(f"%{city_name}%"),
                    Listing.status == 'Active',
                    Listing.last_updated > one_week_ago
                )
                res_stats = await db.execute(q_stats)
                avg_price, max_price = res_stats.first()
                avg_price = int(avg_price) if avg_price else 0
                max_price = int(max_price) if max_price else 0
                
                # 3. Properties that went Pending
                q_pending = select(func.count(Listing.id)).where(
                    Listing.city.ilike(f"%{city_name}%"),
                    Listing.status == 'Pending',
                    Listing.last_updated > one_week_ago
                )
                res_pending = await db.execute(q_pending)
                pending_count = res_pending.scalar() or 0
                
                city_slug = city_name.lower().replace(" ", "-")
                market_url = f"https://gorgerealty.com/market/{city_slug}"
                
                # Formatting values
                avg_price_str = f"${avg_price:,}" if avg_price else "N/A"
                max_price_str = f"${max_price:,}" if max_price else "N/A"
                
                subject = f"Weekly Market Update: {city_name}"
                html_content = f"""
                <div style="font-family: sans-serif; max-width: 600px; border: 1px solid #eee; padding: 20px;">
                    <h2 style="color: #1a5091;">Your {city_name} Market Update</h2>
                    <p>Here is a quick snapshot of the market activity in {city_name} over the last 7 days:</p>
                    
                    <ul style="font-size: 16px; line-height: 1.6; padding-left: 20px; background: #f9f9f9; padding: 15px 15px 15px 35px; border-radius: 5px;">
                        <li><strong>{active_count}</strong> new or updated active listings</li>
                        <li><strong>{pending_count}</strong> homes went pending</li>
                        <li><strong>{avg_price_str}</strong> average listing price</li>
                        <li><strong>{max_price_str}</strong> highest priced new listing</li>
                    </ul>

                    <a href="{market_url}" 
                       style="display: inline-block; background: #1a5091; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 10px;">
                       View Full Analytics Hub
                    </a>
                    <hr style="border: none; border-top: 1px solid #eee; margin-top: 20px;" />
                    <p style="font-size: 12px; color: #999; text-align: center;">
                        Gorge Realty | 123 Main St, Hood River, OR 97031<br />
                        <a href="{unsubscribe_url}" style="color: #999;">Unsubscribe</a> from these alerts
                    </p>
                </div>
                """
                await send_resend_email(lead.email, subject, html_content, db, search, f"market-{city_slug}")

            # ---------------------------------------------------------
            # 3. LEGACY MAP SEARCH ALERT
            # ---------------------------------------------------------
            else:
                query = select(Listing).where(Listing.is_published == True)
                query = query.where(Listing.status == 'Active')
                query = query.where(Listing.created_at > since_time)
                
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
                    city_filters = []
                    for c in criteria['cities']:
                        city_filters.append(Listing.city.ilike(f"%{c}%"))
                        no_spaces = c.replace(' ', '')
                        city_filters.append(Listing.city.ilike(f"%{no_spaces}%"))
                        city_filters.append(func.replace(Listing.city, ' ', '').ilike(f"%{no_spaces}%"))
                    query = query.where(or_(*city_filters))

                match_result = await db.execute(query)
                new_listings = match_result.scalars().all()

                if not new_listings:
                    continue

                log_stmt = select(EmailLog.listing_id).where(EmailLog.search_id == search.id)
                log_result = await db.execute(log_stmt)
                emailed_listing_ids = set(log_result.scalars().all())

                for listing in new_listings:
                    if listing.mls_number in emailed_listing_ids:
                        continue

                    beds_str = f"{listing.beds}" if listing.beds is not None else "—"
                    baths_str = f"{listing.baths}" if listing.baths is not None else "—"
                    sqft_str = f"{listing.sqft:,}" if listing.sqft is not None else "—"
                    
                    slug = create_slug(listing.address)
                    property_url = f"https://gorgerealty.com/property/{slug}/{listing.mls_number}"

                    subject = f"New Match: {listing.address} - ${listing.price:,}"
                    html_content = f"""
                    <div style="font-family: sans-serif; max-width: 600px; border: 1px solid #eee; padding: 20px;">
                        <h2 style="color: #1a5091;">A new home matches your search!</h2>
                        <img src="{listing.photo_url}" style="width: 100%; border-radius: 8px;" />
                        <h3 style="margin-top: 15px;">{listing.address}</h3>
                        <p style="font-size: 18px; font-weight: bold;">${listing.price:,}</p>
                        <p>{beds_str} Beds | {baths_str} Baths | {sqft_str} SqFt</p>
                        <a href="{property_url}" 
                           style="display: inline-block; background: #1a5091; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 10px;">
                           View Full Listing Details
                        </a>
                        <hr style="border: none; border-top: 1px solid #eee; margin-top: 20px;" />
                        <p style="font-size: 12px; color: #999; text-align: center;">
                            Gorge Realty | 123 Main St, Hood River, OR 97031<br />
                            <a href="{unsubscribe_url}" style="color: #999;">Unsubscribe</a> from these alerts
                        </p>
                    </div>
                    """
                    await send_resend_email(lead.email, subject, html_content, db, search, str(listing.mls_number))

if __name__ == "__main__":
    asyncio.run(process_alerts())
