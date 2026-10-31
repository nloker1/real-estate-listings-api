"""Private reporting from verified events, separate from legacy tracking totals."""
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, unquote

from fastapi import HTTPException
from sqlalchemy import select, func, case, or_

from app.models import Lead, SavedSearch, EmailLog, EmailEvent, Listing


def utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def page_result(items, total, page, page_size):
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size)}


def real_log():
    # Backfill rows deliberately represent inventory suppression, not email sends.
    return or_(EmailLog.message_id.is_(None), EmailLog.message_id != "BACKFILL_DRY_RUN")


def client_fields(lead):
    return {"id": lead.id, "name": lead.name, "email": lead.email,
            "created_at": utc(lead.created_at), "is_unsubscribed": bool(lead.is_unsubscribed)}


async def require_client(db, client_id):
    lead = await db.get(Lead, client_id)
    if lead is None:
        raise HTTPException(404, "Client not found")
    return lead


async def summaries(db, ids):
    if not ids:
        return {}
    result = {key: {"search_count": 0, "active_search_count": 0, "email_count": 0,
                    "last_email_at": None, "recorded_open_count": 0, "recorded_click_count": 0,
                    "last_recorded_open_at": None, "last_recorded_click_at": None} for key in ids}
    rows = await db.execute(select(SavedSearch.lead_id, func.count(SavedSearch.id),
        func.sum(case((SavedSearch.is_active == True, 1), else_=0))).where(
            SavedSearch.lead_id.in_(ids)).group_by(SavedSearch.lead_id))
    for lead_id, count, active in rows:
        result[lead_id].update(search_count=count, active_search_count=active)
    rows = await db.execute(select(SavedSearch.lead_id, func.count(EmailLog.id), func.max(EmailLog.sent_at))
        .join(EmailLog, EmailLog.search_id == SavedSearch.id)
        .where(SavedSearch.lead_id.in_(ids), real_log()).group_by(SavedSearch.lead_id))
    for lead_id, count, last in rows:
        result[lead_id].update(email_count=count, last_email_at=utc(last))
    rows = await db.execute(select(SavedSearch.lead_id, EmailEvent.event_type,
                                   func.count(EmailEvent.id), func.max(EmailEvent.occurred_at))
        .join(EmailLog, EmailLog.search_id == SavedSearch.id)
        .join(EmailEvent, EmailEvent.email_log_id == EmailLog.id)
        .where(SavedSearch.lead_id.in_(ids), real_log(), EmailEvent.processing_status == "processed",
               EmailEvent.event_type.in_(["email.opened", "email.clicked"]))
        .group_by(SavedSearch.lead_id, EmailEvent.event_type))
    for lead_id, kind, count, last in rows:
        key = "open" if kind == "email.opened" else "click"
        result[lead_id][f"recorded_{key}_count"] = count
        result[lead_id][f"last_recorded_{key}_at"] = utc(last)
    return result


async def clients(db, q, page, page_size):
    query = select(Lead)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(Lead.email.ilike(pattern, escape="\\"), Lead.name.ilike(pattern, escape="\\")))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    leads = (await db.execute(query.order_by(Lead.created_at.desc(), Lead.id.desc())
                            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    stats = await summaries(db, [lead.id for lead in leads])
    return page_result([{**client_fields(lead), **stats[lead.id]} for lead in leads], total, page, page_size)


async def activity(db, days, page, page_size):
    """One row per engaged client, ordered by latest verified provider activity."""
    now = datetime.now(timezone.utc)
    query = select(SavedSearch.lead_id.label('client_id'), EmailLog.id.label('email_id'),
                   EmailLog.listing_id, EmailEvent.id.label('event_id'), EmailEvent.event_type,
                   EmailEvent.occurred_at, EmailEvent.details).join(
                       EmailLog, EmailLog.search_id == SavedSearch.id).join(
                       EmailEvent, EmailEvent.email_log_id == EmailLog.id).where(
                           real_log(), EmailEvent.processing_status == 'processed',
                           EmailEvent.event_type.in_(['email.opened', 'email.clicked']),
                           EmailEvent.occurred_at <= now)
    if days:
        query = query.where(EmailEvent.occurred_at >= now - timedelta(days=days))
    eligible = query.subquery()
    grouped = select(eligible.c.client_id,
        func.max(eligible.c.occurred_at).label('last_activity_at'),
        func.sum(case((eligible.c.event_type == 'email.opened', 1), else_=0)).label('opens'),
        func.sum(case((eligible.c.event_type == 'email.clicked', 1), else_=0)).label('clicks'),
    ).group_by(eligible.c.client_id).subquery()
    # Joining Lead excludes orphaned searches from both summary and page totals.
    visible = select(grouped).join(Lead, Lead.id == grouped.c.client_id).subquery()
    totals = (await db.execute(select(func.count(), func.coalesce(func.sum(visible.c.opens), 0),
        func.coalesce(func.sum(visible.c.clicks), 0)).select_from(visible))).one()
    rows = (await db.execute(select(Lead, visible.c.last_activity_at, visible.c.opens, visible.c.clicks)
        .join(visible, visible.c.client_id == Lead.id)
        .order_by(visible.c.last_activity_at.desc(), Lead.id.desc())
        .offset((page - 1) * page_size).limit(page_size))).all()
    ids = [lead.id for lead, *_ in rows]
    latest = {}
    if ids:
        ranked = select(eligible, func.row_number().over(partition_by=eligible.c.client_id,
            order_by=(eligible.c.occurred_at.desc(), eligible.c.event_id.desc())).label('rank'))
        ranked = ranked.where(eligible.c.client_id.in_(ids)).subquery()
        latest_rows = await db.execute(select(ranked, Listing.address.label('listing_address'))
            .outerjoin(Listing, Listing.mls_number == ranked.c.listing_id).where(ranked.c.rank == 1))
        latest = {row.client_id: row for row in latest_rows}
    items = []
    for lead, last, opens, clicks in rows:
        event = latest[lead.id]
        kind = ('email_open' if event.event_type == 'email.opened' else 'listing_click'
                if listing_click(event.details.get('link'), event.listing_id) else 'link_click')
        items.append({**client_fields(lead), 'last_activity_at': utc(last),
                      'recorded_open_count': opens, 'recorded_click_count': clicks,
                      'last_activity_kind': kind, 'last_email_id': event.email_id,
                      'listing_id': event.listing_id, 'listing_address': event.listing_address})
    return {**page_result(items, totals[0], page, page_size), 'days': days,
            'summary': {'active_clients': totals[0], 'recorded_opens': totals[1], 'recorded_clicks': totals[2]}}


async def client(db, client_id):
    lead = await require_client(db, client_id)
    stats = await summaries(db, [lead.id])
    return {**client_fields(lead), **stats[lead.id]}


async def searches(db, client_id, page, page_size):
    await require_client(db, client_id)
    query = select(SavedSearch).where(SavedSearch.lead_id == client_id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await db.execute(query.order_by(SavedSearch.created_at.desc(), SavedSearch.id.desc())
                           .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    # Criteria are an allowlist: arbitrary signup JSON is not a private-data export.
    keys = {"alert_type", "mls_number", "city", "cities", "search", "propertyType", "minPrice", "maxPrice",
            "minBeds", "minBaths", "minSqft", "maxSqft", "minAcres", "maxAcres"}
    def visible_criteria(criteria):
        output = {}
        for key, value in criteria.items():
            if key not in keys:
                continue
            if key == "cities" and isinstance(value, list):
                output[key] = [city[:100] for city in value[:100] if isinstance(city, str)]
            elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
                output[key] = value[:200] if isinstance(value, str) else value
        return output
    return page_result([{"id": row.id, "is_active": bool(row.is_active), "frequency": row.frequency,
                         "created_at": utc(row.created_at), "last_alert_sent": utc(row.last_alert_sent),
                         "criteria": visible_criteria(row.criteria)}
                        for row in rows], total, page, page_size)


async def log_for_client(db, client_id, email_id):
    row = (await db.execute(select(EmailLog).join(SavedSearch).where(
        SavedSearch.lead_id == client_id, EmailLog.id == email_id, real_log()))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Email not found for this client")
    return row


async def email_rows(db, logs):
    if not logs:
        return []
    ids = [row.id for row in logs]
    stats = {key: {"recorded_open_count": 0, "recorded_click_count": 0,
                   "first_recorded_open_at": None, "first_recorded_click_at": None,
                   "delivered_at": None, "delivery_status": None} for key in ids}
    grouped = await db.execute(select(EmailEvent.email_log_id, EmailEvent.event_type,
        func.count(EmailEvent.id), func.min(EmailEvent.occurred_at)).where(
        EmailEvent.email_log_id.in_(ids), EmailEvent.processing_status == "processed"
    ).group_by(EmailEvent.email_log_id, EmailEvent.event_type))
    for log_id, kind, count, first in grouped:
        if kind in {"email.opened", "email.clicked"}:
            key = "open" if kind == "email.opened" else "click"
            stats[log_id][f"recorded_{key}_count"] = count
            stats[log_id][f"first_recorded_{key}_at"] = utc(first)
        elif kind == "email.delivered":
            stats[log_id]["delivered_at"] = utc(first)
    # Window ranking chooses by provider event time, not arrival time.
    delivery_types = ["email.sent", "email.scheduled", "email.delivered", "email.delivery_delayed",
                      "email.failed", "email.bounced", "email.complained", "email.suppressed"]
    ranked = select(EmailEvent.email_log_id, EmailEvent.event_type, func.row_number().over(
        partition_by=EmailEvent.email_log_id,
        order_by=(EmailEvent.occurred_at.desc(), EmailEvent.id.desc())).label("rank")).where(
        EmailEvent.email_log_id.in_(ids), EmailEvent.processing_status == "processed",
        EmailEvent.event_type.in_(delivery_types)).subquery()
    for log_id, kind in await db.execute(select(ranked.c.email_log_id, ranked.c.event_type).where(ranked.c.rank == 1)):
        stats[log_id]["delivery_status"] = kind.removeprefix("email.")
    listings = (await db.execute(select(Listing.mls_number, Listing.address).where(
        Listing.mls_number.in_([row.listing_id for row in logs])))).all()
    addresses = dict(listings)
    return [{"id": row.id, "search_id": row.search_id, "listing_id": row.listing_id,
             "listing_address": addresses.get(row.listing_id), "recipient": row.user_email,
             "accepted_at": utc(row.sent_at), "provider_message_id": row.message_id,
             "legacy_open_count": row.open_count or 0, "legacy_click_count": row.click_count or 0,
             **{**stats[row.id], "delivery_status": stats[row.id]["delivery_status"] or
                ("accepted" if row.message_id else "logged_unverified")}}
            for row in logs]


async def emails(db, client_id, page, page_size, opened_only=False, days=0):
    await require_client(db, client_id)
    query = select(EmailLog).join(SavedSearch).where(SavedSearch.lead_id == client_id, real_log())
    opened = None
    if opened_only:
        now = datetime.now(timezone.utc)
        events = select(EmailEvent.email_log_id.label('log_id'),
                        func.count(EmailEvent.id).label('opens'),
                        func.max(EmailEvent.occurred_at).label('last_open_at')).where(
            EmailEvent.processing_status == 'processed', EmailEvent.event_type == 'email.opened',
            EmailEvent.occurred_at <= now)
        if days:
            events = events.where(EmailEvent.occurred_at >= now - timedelta(days=days))
        opened = events.group_by(EmailEvent.email_log_id).subquery()
        query = query.join(opened, opened.c.log_id == EmailLog.id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    order = (opened.c.last_open_at.desc(), EmailLog.id.desc()) if opened_only else (EmailLog.sent_at.desc(), EmailLog.id.desc())
    if opened_only:
        rows = (await db.execute(query.add_columns(opened.c.opens, opened.c.last_open_at)
            .order_by(*order).offset((page - 1) * page_size).limit(page_size))).all()
        items = await email_rows(db, [row[0] for row in rows])
        for item, (_, opens, last_open) in zip(items, rows):
            item.update(period_open_count=opens, last_recorded_open_at=utc(last_open))
    else:
        logs = (await db.execute(query.order_by(*order)
            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
        items = await email_rows(db, logs)
    return page_result(items, total, page, page_size)


async def email(db, client_id, email_id):
    return (await email_rows(db, [await log_for_client(db, client_id, email_id)]))[0]


def listing_click(link, listing_id):
    try:
        parsed = urlsplit(link or "")
        path = unquote(parsed.path).strip("/").split("/")
        return (parsed.scheme in {"http", "https"} and parsed.hostname in {
            "gorgerealty.com", "www.gorgerealty.com", "lokerrealty.com", "www.lokerrealty.com"}
            and len(path) == 3 and path[0] == "property" and path[2] == listing_id)
    except ValueError:
        return False


async def events(db, client_id, email_id, page, page_size):
    log = await log_for_client(db, client_id, email_id)
    query = select(EmailEvent).where(EmailEvent.email_log_id == log.id, EmailEvent.processing_status == "processed")
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await db.execute(query.order_by(EmailEvent.occurred_at.desc(), EmailEvent.id.desc())
                            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return page_result([{"id": row.id, "event_type": row.event_type,
                         "occurred_at": utc(row.occurred_at), "received_at": utc(row.received_at),
                         "clicked_url": row.details.get("link") if row.event_type == "email.clicked" else None,
                         "is_listing_click": row.event_type == "email.clicked" and listing_click(row.details.get("link"), log.listing_id),
                         "engagement_source": "unclassified" if row.event_type in {"email.opened", "email.clicked"} else None}
                        for row in rows], total, page, page_size)
