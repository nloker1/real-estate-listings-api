import os
import asyncio
import resend
from datetime import datetime, timedelta
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload
from app.database import AsyncSessionLocal
from app.models import Listing, Lead, SavedSearch

resend.api_key = os.getenv("RESEND_API_KEY")

async def generate_daily_digest():
    print("Generating Daily Gorge Digest...")
    
    # Time window: Last 24 hours
    yesterday = datetime.utcnow() - timedelta(days=3)
    
    async with AsyncSessionLocal() as db:
        # 1. NEW LISTINGS (Created in last 24h)
        new_stmt = (
            select(Listing)
            .where(Listing.is_published == True)
            .where(Listing.status == 'Active')
            .where(Listing.created_at >= yesterday)
            .where(Listing.city.in_(['HoodRiver', 'WhiteSalmon']))
            .order_by(Listing.price.desc())
        )
        new_listings = (await db.execute(new_stmt)).scalars().all()

        # 2. STATUS CHANGES (Actually changed in last 24h)
        # We look for:
        # - Sold listings with a CloseDate in the last 24h
        # - Non-Active listings with a StatusDate (StatusChangeTimestamp) in the last 24h
        off_market_stmt = (
            select(Listing)
            .where(Listing.status != 'Active')
            .where(
                or_(
                    Listing.close_date >= yesterday.date(),
                    Listing.status_date >= yesterday
                )
            )
            .where(Listing.city.in_(['HoodRiver', 'WhiteSalmon']))
        )
        off_market_listings = (await db.execute(off_market_stmt)).scalars().all()

        # 3. NEW LEADS & SEARCHES (Last 24h)
        new_leads_stmt = select(Lead).where(Lead.created_at >= yesterday)
        new_leads = (await db.execute(new_leads_stmt)).scalars().all()

        new_searches_stmt = (
            select(SavedSearch)
            .options(joinedload(SavedSearch.lead))
            .where(SavedSearch.created_at >= yesterday)
        )
        new_searches = (await db.execute(new_searches_stmt)).scalars().all()

        # 4. MARKET SNAPSHOT
        total_active_stmt = select(Listing).where(Listing.status == 'Active').where(Listing.city.in_(['HoodRiver', 'WhiteSalmon']))
        total_active = len((await db.execute(total_active_stmt)).scalars().all())

        if not new_listings and not off_market_listings and not new_leads and not new_searches:
            print("No significant changes in the last 24h. Skipping email.")
            return

        # --- BUILD EMAIL HTML ---
        html_content = f"""
        <div style="font-family: sans-serif; max-width: 650px; margin: auto; color: #333;">
            <div style="background: #1a5091; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 24px;">Gorge Real Estate Daily Digest</h1>
                <p style="margin: 5px 0 0; opacity: 0.9;">{datetime.now().strftime('%B %d, %Y')}</p>
            </div>
            
            <div style="padding: 20px; border: 1px solid #eee; border-top: none;">
                <p style="font-size: 16px;">Here is your market update for <b>Hood River</b> and <b>White Salmon</b>.</p>
                
                <!-- SUMMARY TABLE (Replaced Flex for better email compatibility) -->
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 30px; background: #f9f9f9; border-radius: 5px;">
                    <tr>
                        <td align="center" width="33%" style="padding: 20px 0;">
                            <span style="display: block; font-size: 24px; font-weight: bold; color: #1a5091;">{len(new_listings)}</span>
                            <span style="display: block; font-size: 11px; text-transform: uppercase; color: #666; margin-top: 5px;">New Listings</span>
                        </td>
                        <td align="center" width="33%" style="padding: 20px 0; border-left: 1px solid #ddd; border-right: 1px solid #ddd;">
                            <span style="display: block; font-size: 24px; font-weight: bold; color: #d9534f;">{len(off_market_listings)}</span>
                            <span style="display: block; font-size: 11px; text-transform: uppercase; color: #666; margin-top: 5px;">Off-Market</span>
                        </td>
                        <td align="center" width="33%" style="padding: 20px 0;">
                            <span style="display: block; font-size: 24px; font-weight: bold; color: #5bc0de;">{total_active}</span>
                            <span style="display: block; font-size: 11px; text-transform: uppercase; color: #666; margin-top: 5px;">Total Active</span>
                        </td>
                    </tr>
                </table>

                <!-- SECTION: NEW LEADS & ACTIVITY -->
                <h2 style="border-bottom: 2px solid #5cb85c; padding-bottom: 5px; color: #5cb85c;">👥 New Leads & Activity</h2>
                <div style="margin-bottom: 30px; background: #fdfdfd; padding: 15px; border: 1px solid #f0f0f0; border-radius: 4px;">
                    <p style="margin: 5px 0; font-size: 14px;"><b>New Subscribers:</b> {len(new_leads)}</p>
                    <p style="margin: 5px 0; font-size: 14px;"><b>New Saved Searches:</b> {len(new_searches)}</p>
                    
                    {f'<div style="margin-top: 10px; font-size: 13px; color: #666; border-top: 1px solid #eee; padding-top: 10px;">' if new_leads or new_searches else ''}
                    {"".join([f"<div>• <b>{lead.email}</b> joined the site</div>" for lead in new_leads])}
                    {"".join([f"<div>• <b>{s.lead.email if s.lead else 'Unknown'}</b> set up a new {s.frequency} search</div>" for s in new_searches])}
                    {f'</div>' if new_leads or new_searches else ''}
                </div>

                <h2 style="border-bottom: 2px solid #1a5091; padding-bottom: 5px; color: #1a5091;">✨ New Listings</h2>
        """

        if not new_listings:
            html_content += "<p style='color: #888;'>No new listings today.</p>"
        else:
            for l in new_listings:
                beds = l.beds if l.beds else "—"
                baths = l.baths if l.baths else "—"
                html_content += f"""
                <div style="margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 15px;">
                    <table width="100%">
                        <tr>
                            <td width="150" valign="top">
                                <img src="{l.photo_url}" style="width: 140px; height: 100px; object-fit: cover; border-radius: 4px;">
                            </td>
                            <td valign="top" style="padding-left: 15px;">
                                <h3 style="margin: 0; color: #333; font-size: 18px;">{l.address}</h3>
                                <p style="margin: 5px 0; color: #1a5091; font-weight: bold; font-size: 16px;">${l.price:,}</p>
                                <p style="margin: 0; font-size: 14px; color: #666;">{l.city} | {beds} Bed | {baths} Bath</p>
                                <a href="https://gorgerealty.com/property/view/{l.mls_number}" style="color: #1a5091; font-size: 13px; text-decoration: none; font-weight: bold;">View Details →</a>
                            </td>
                        </tr>
                    </table>
                </div>
                """

        html_content += """
                <h2 style="border-bottom: 2px solid #d9534f; padding-bottom: 5px; color: #d9534f; margin-top: 40px;">🚫 Status Changes</h2>
        """

        if not off_market_listings:
            html_content += "<p style='color: #888;'>No status changes today.</p>"
        else:
            for l in off_market_listings:
                status_color = "#d9534f" if l.status in ['Sold', 'Off-Market'] else "#f0ad4e"
                html_content += f"""
                <div style="margin-bottom: 10px; font-size: 14px;">
                    <span style="color: {status_color}; font-weight: bold; text-transform: uppercase; font-size: 12px;">[{l.status}]</span>
                    <span style="margin-left: 10px;">{l.address} - <b>${l.price:,}</b> ({l.city})</span>
                </div>
                """

        html_content += """
                <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; font-size: 12px; color: #999; text-align: center;">
                    This is an automated digest from your Gorge Realty system.
                </div>
            </div>
        </div>
        """

        # --- SEND THE EMAIL ---
        try:
            params = {
                "from": "Gorge Realty Digest <alerts@gorgerealty.com>",
                "to": ["nloker1@gmail.com"], # You can change this or add others!
                "subject": f"Gorge Realty Daily Digest: {datetime.now().strftime('%b %d')}",
                "html": html_content,
            }
            resend.Emails.send(params)
            print("Digest sent successfully.")
        except Exception as e:
            print(f"Error sending digest: {e}")

if __name__ == "__main__":
    asyncio.run(generate_daily_digest())
