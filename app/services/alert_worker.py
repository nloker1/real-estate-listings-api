from app.preview import require_writable_mode
import os
import asyncio
import re
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_, func
from sqlalchemy.orm import joinedload
from app.models import SavedSearch, Listing, EmailLog, Lead
from app.services.unsubscribe import ensure_unsubscribe_token

def send_with_resend(params):
    require_writable_mode()
    if os.getenv("APP_ENV", "").strip().lower() == "test":
        raise RuntimeError("Real email delivery is disabled in test mode")
    import resend
    resend.api_key = os.getenv("RESEND_API_KEY")
    return resend.Emails.send(params)

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

async def send_resend_email(to_email, subject, html_content, db, search, listing_id_str,
                            *, sender, now, delay, criteria_on_success=None):
    params = {
        "from": "Gorge Realty Alerts <alerts@gorgerealty.com>",
        "to": [to_email], "subject": subject, "html": html_content,
    }
    try:
        response = sender(params)
    except Exception:
        # No DB changes have occurred; rollback would expire loaded ORM objects.
        print(f"Provider failed for search {search.id}; retry on next run.")
        await asyncio.sleep(delay)
        return False
    if not response or not response.get("id"):
        print(f"No message ID for search {search.id}; retry on next run.")
        await asyncio.sleep(delay)
        return False
    db.add(EmailLog(search_id=search.id, listing_id=listing_id_str,
                    user_email=to_email, message_id=response["id"]))
    search.last_alert_sent = naive_utc(now())
    if criteria_on_success is not None:
        search.criteria = criteria_on_success
    # Commit the baseline and log together. Persistence failures stop the job.
    await db.commit()
    await asyncio.sleep(delay)
    return True


def naive_utc(value):
    # Current DB columns are naive; normalize aware clock inputs before comparing.
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


async def process_alerts(session_factory=None, sender=None, now=None, delay=0.9,
                         test_lead_email=None):
    require_writable_mode()
    test_mode = os.getenv("APP_ENV", "").strip().lower() == "test"
    if test_mode and (session_factory is None or sender is None):
        raise RuntimeError("Test mode requires an explicit database session factory and fake sender")
    if session_factory is None:
        from app.database import AsyncSessionLocal
        session_factory = AsyncSessionLocal
    sender = sender or send_with_resend
    now = now or datetime.utcnow
    print("Checking for Saved Search Matches...")
    async with session_factory() as db:
        
        stmt = (
            select(SavedSearch)
            .join(Lead)
            .options(joinedload(SavedSearch.lead))
            .where(SavedSearch.is_active == True)
            .where(Lead.is_unsubscribed == False)
        )
        
        # Optional recipient filter; not a substitute for isolated DB/email dependencies.
        if test_lead_email:
            stmt = stmt.where(Lead.email == test_lead_email)
            
        result = await db.execute(stmt)
        searches = result.scalars().all()
        
        print(f"Found {len(searches)} active searches.")

        for search in searches:
            lead = search.lead 
            if not lead or not lead.email:
                continue

            criteria = search.criteria
            alert_type = criteria.get("alert_type")
            
            current_time = naive_utc(now())
                
            if not lead.unsubscribe_token:
                await ensure_unsubscribe_token(db, lead)
                await db.commit()
            unsubscribe_url = f"https://gorgerealty.com/unsubscribe?token={lead.unsubscribe_token}"

            # ---------------------------------------------------------
            # 1. PROPERTY ALERT
            # ---------------------------------------------------------
            if alert_type == "property" or criteria.get("mls_number"):
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
                        # Apply the new baseline only after provider acceptance.
                        new_criteria = dict(criteria)
                        new_criteria["last_price"] = updated_listing.price
                        new_criteria["last_status"] = updated_listing.status
                        
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
                        await send_resend_email(lead.email, subject, html_content, db, search, str(mls_number),
                                                sender=sender, now=now, delay=delay,
                                                criteria_on_success=new_criteria)

            # ---------------------------------------------------------
            # 2. MARKET ALERT
            # ---------------------------------------------------------
            elif alert_type == "market":
                # Only send weekly
                since_time = naive_utc(search.last_alert_sent or search.created_at)
                if (current_time - since_time) < timedelta(days=7):
                    continue
                    
                city_name = criteria.get("city")
                if not city_name:
                    continue
                    
                # Grab statistics for that city in the last week to make it interesting
                one_week_ago = current_time - timedelta(days=7)
                
                # 1. New/Updated Active Count
                q_active = select(func.count(Listing.id)).where(
                    func.replace(Listing.city, " ", "").ilike(city_name.replace(" ", "")),
                    Listing.is_published == True,
                    Listing.status == 'Active',
                    Listing.last_updated > one_week_ago,
                    Listing.last_updated <= current_time
                )
                res_active = await db.execute(q_active)
                active_count = res_active.scalar() or 0
                
                # 2. Average & Max Price of those Active Listings
                q_stats = select(
                    func.avg(Listing.price),
                    func.max(Listing.price)
                ).where(
                    func.replace(Listing.city, " ", "").ilike(city_name.replace(" ", "")),
                    Listing.is_published == True,
                    Listing.status == 'Active',
                    Listing.last_updated > one_week_ago,
                    Listing.last_updated <= current_time
                )
                res_stats = await db.execute(q_stats)
                avg_price, max_price = res_stats.first()
                avg_price = int(avg_price) if avg_price else 0
                max_price = int(max_price) if max_price else 0
                
                # 3. Currently pending properties whose status changed this week
                q_pending = select(func.count(Listing.id)).where(
                    func.replace(Listing.city, " ", "").ilike(city_name.replace(" ", "")),
                    Listing.is_published == True,
                    Listing.status == 'Pending',
                    Listing.status_date > one_week_ago,
                    Listing.status_date <= current_time
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
                        <li><strong>{pending_count}</strong> currently pending homes with a status change this week</li>
                        <li><strong>{avg_price_str}</strong> average listing price</li>
                        <li><strong>{max_price_str}</strong> highest priced new or updated active listing</li>
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
                await send_resend_email(lead.email, subject, html_content, db, search, f"market-{city_slug}",
                                        sender=sender, now=now, delay=delay)

            # ---------------------------------------------------------
            # 3. LEGACY MAP SEARCH ALERT
            # ---------------------------------------------------------
            else:
                query = select(Listing).where(Listing.is_published == True)
                query = query.where(Listing.status == 'Active')
                query = query.where(Listing.created_at > naive_utc(search.created_at))
                query = query.where(~select(EmailLog.id).where(
                    EmailLog.search_id == search.id,
                    EmailLog.listing_id == Listing.mls_number,
                ).exists())
                
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

                # --- ADDED FILTERS BELOW ---
                if criteria.get('minSqft'):
                    query = query.where(Listing.sqft >= int(criteria['minSqft']))
                if criteria.get('maxSqft'):
                    query = query.where(Listing.sqft <= int(criteria['maxSqft']))
                if criteria.get('minAcres'):
                    query = query.where(Listing.acreage >= float(criteria['minAcres']))
                if criteria.get('maxAcres'):
                    query = query.where(Listing.acreage <= float(criteria['maxAcres']))
                # ---------------------------
                
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

                for listing in new_listings:
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
                            Gorge Realty | White Salmon, WA 98672 <br />
                            <a href="{unsubscribe_url}" style="color: #999;">Unsubscribe</a> from these alerts
                        </p>
                    </div>
                    """
                    await send_resend_email(lead.email, subject, html_content, db, search, str(listing.mls_number),
                                            sender=sender, now=now, delay=delay)

if __name__ == "__main__":
    asyncio.run(process_alerts())
