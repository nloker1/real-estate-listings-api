"""RealScout contact import. Default: offline CSV validation, no DB or email access.

--check-database: read-only comparison. --apply: atomic contact import.
Deleted? is source metadata only; it does not establish subscription preferences.
Existing unsubscribe flags and dates are never changed.
"""
import argparse
import asyncio
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError


EMAIL = TypeAdapter(EmailStr)
REQUIRED = {'Contact ID', 'Contact Email', 'First Name', 'Last Name', 'Deleted?',
            'Listing Alert Count', 'Market Alert Count', 'Home Value Alert Count'}


class ImportProblem(Exception):
    """Only generic, non-personal error messages may be printed."""


@dataclass(frozen=True)
class Contact:
    line: int
    contact_id: str = field(repr=False)
    email: str = field(repr=False)
    name: str = field(repr=False)
    phone: str | None = field(repr=False)
    deleted: bool
    listing_alerts: int
    market_alerts: int
    home_value_alerts: int


def normalize_phone(value):
    if not value or not str(value).strip():
        return None
    digits = re.sub(r'\D', '', str(value))
    if len(digits) == 10:
        return '+1' + digits
    if len(digits) == 11 and digits.startswith('1'):
        return '+' + digits
    return None


def load_contacts(path):
    contacts, issues = [], []
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as source:
            reader = csv.DictReader(source)
            headers = reader.fieldnames or []
            if not REQUIRED.issubset(headers) or len(headers) != len(set(headers)):
                raise ImportProblem('CSV has missing or duplicate column headers.')
            for row in reader:
                line = reader.line_num
                if None in row or any(value is None for value in row.values()):
                    issues.append({'line': line, 'reason': 'malformed CSV row'})
                    continue
                try:
                    email = str(EMAIL.validate_python(row['Contact Email'].strip())).lower()
                    source_id = row['Contact ID'].strip()
                    if not source_id or len(source_id) > 255:
                        raise ValueError()
                    flag = row['Deleted?'].strip().lower()
                    if flag not in {'true', 'false', 'yes', 'no', '1', '0'}:
                        raise ValueError()
                    counts = [row[key].strip() for key in ('Listing Alert Count', 'Market Alert Count', 'Home Value Alert Count')]
                    if any(not re.fullmatch(r'\d{1,9}', count) for count in counts):
                        raise ValueError()
                    name = ' '.join((row['First Name'].strip(), row['Last Name'].strip())).strip()
                    if len(name) > 200:
                        raise ValueError()
                    phone = next((row.get(key, '').strip() for key in (
                        'Primary Mobile Phone', 'Primary Home Phone', 'Primary Work Phone') if row.get(key, '').strip()), '')
                    normalized_phone = normalize_phone(phone)
                    if phone and normalized_phone is None:
                        issues.append({'line': line, 'reason': 'phone omitted: unsupported format', 'warning': True})
                    contacts.append(Contact(line, source_id, email, name, normalized_phone,
                                            flag in {'true', 'yes', '1'}, *map(int, counts)))
                except (ValidationError, ValueError):
                    issues.append({'line': line, 'reason': 'invalid email, ID, deletion flag, name, or alert count'})
    except (OSError, UnicodeError, csv.Error):
        raise ImportProblem('Could not read the CSV. Check its location and UTF-8 format.') from None
    for attr in ('email', 'contact_id'):
        counts = Counter(getattr(contact, attr) for contact in contacts)
        for contact in contacts:
            if counts[getattr(contact, attr)] > 1:
                issues.append({'line': contact.line, 'reason': f'duplicate {attr} within CSV'})
    if not contacts:
        issues.append({'reason': 'CSV contains no valid contacts'})
    return contacts, issues


def offline_report(contacts, issues):
    return {'mode': 'offline_validation', 'database_checked': False, 'applied': False,
            'contacts': len(contacts), 'source_marked_deleted': sum(c.deleted for c in contacts),
            'listing_alerts_to_review': sum(c.listing_alerts for c in contacts),
            'home_value_alerts_not_supported': sum(c.home_value_alerts for c in contacts),
            'shared_phone_groups': sum(n > 1 for n in Counter(c.phone for c in contacts if c.phone).values()),
            'issues': issues, 'errors': sum(not issue.get('warning') for issue in issues)}


async def import_contacts(db, contacts, apply=False):
    """Caller owns transaction. No provider imports, sends, or search writes."""
    from sqlalchemy import select, text
    from app.models import Lead, RealScoutContactImport
    from app.preview import is_preview
    if apply and is_preview():
        raise ImportProblem('Applying imports is disabled in read-only preview mode.')
    if apply:
        # Serialize importer runs; existing signup writers are still protected by
        # unique email constraints, with any conflict rolling back the whole batch.
        await db.execute(text('SELECT pg_advisory_xact_lock(72410321)'))
    leads = (await db.execute(select(Lead))).scalars().all()
    mappings = {row.contact_id: row for row in (await db.execute(select(RealScoutContactImport))).scalars()}
    by_email = defaultdict(list)
    phone_owners = defaultdict(set)
    by_id = {lead.id: lead for lead in leads}
    for lead in leads:
        if lead.email:
            by_email[lead.email.strip().lower()].append(lead)
        phone = normalize_phone(lead.phone)
        if phone:
            phone_owners[phone].add(lead.id)
    csv_phones = Counter(c.phone for c in contacts if c.phone)
    report = {'mode': 'apply' if apply else 'database_dry_run', 'database_checked': True, 'applied': False,
              'contacts': len(contacts), 'new_clients': 0, 'existing_clients': 0,
              'source_marked_deleted': sum(c.deleted for c in contacts),
              'new_clients_needing_preference_review': 0, 'existing_unsubscribes_preserved': 0,
              'names_to_fill': 0, 'phones_to_fill': 0, 'phones_omitted': 0,
              'tokens_to_repair': 0, 'new_source_mappings': 0, 'conflicts': [], 'rows': []}
    plans = []
    for contact in contacts:
        matches = by_email[contact.email]
        mapping = mappings.get(contact.contact_id)
        lead = matches[0] if len(matches) == 1 else None
        conflict = None
        if len(matches) > 1:
            conflict = 'multiple database clients have the same normalized email'
        elif mapping and (mapping.lead_id not in by_id or not lead or mapping.lead_id != lead.id):
            conflict = 'source ID maps to a different email/client; manual review required'
        elif lead and any(m.contact_id != contact.contact_id and m.lead_id == lead.id for m in mappings.values()):
            conflict = 'client already maps to a different RealScout contact ID'
        if conflict:
            report['conflicts'].append({'line': contact.line, 'reason': conflict})
            continue
        report['existing_clients' if lead else 'new_clients'] += 1
        if not lead: report['new_clients_needing_preference_review'] += 1
        if lead and lead.is_unsubscribed: report['existing_unsubscribes_preserved'] += 1
        if lead and not (lead.name or '').strip() and contact.name: report['names_to_fill'] += 1
        if lead and not lead.unsubscribe_token: report['tokens_to_repair'] += 1
        phone = contact.phone
        if phone and (csv_phones[phone] > 1 or phone_owners[phone] - ({lead.id} if lead else set())):
            phone = None
            report['phones_omitted'] += 1
        if phone and (not lead or not lead.phone): report['phones_to_fill'] += 1
        if not mapping: report['new_source_mappings'] += 1
        plans.append((contact, lead, mapping, phone))
        report['rows'].append({'line': contact.line, 'action': 'match_existing' if lead else 'create',
                               'lead_id': lead.id if lead else None, 'phone_omitted': bool(contact.phone and not phone), 'unsubscribed': bool(lead and lead.is_unsubscribed),
                               'source_deleted': contact.deleted, 'source_subscription_status': 'unknown'})
    if report['conflicts'] or not apply:
        return report
    from app.services.unsubscribe import ensure_unsubscribe_token
    now = datetime.now(timezone.utc)
    for index, (contact, lead, mapping, phone) in enumerate(plans):
        if lead:
            # Refresh under row lock so concurrent unsubscribe/name edits are not
            # overwritten using an earlier planning snapshot.
            lead = (await db.execute(select(Lead).where(Lead.id == lead.id).with_for_update()
                                    .execution_options(populate_existing=True))).scalar_one()
            if (lead.email or '').strip().lower() != contact.email:
                raise ImportProblem('A client email changed during import. No batch changes were committed; compare again.')
        else:
            lead = Lead(email=contact.email, name=contact.name or None, phone=phone,
                        is_unsubscribed=False)
            db.add(lead)
            await db.flush()
        if not (lead.name or '').strip() and contact.name: lead.name = contact.name
        if not lead.phone and phone: lead.phone = phone
        await ensure_unsubscribe_token(db, lead)
        if mapping is None:
            mapping = RealScoutContactImport(contact_id=contact.contact_id, lead_id=lead.id)
            db.add(mapping)
        mapping.source_deleted = contact.deleted
        mapping.listing_alert_count = contact.listing_alerts
        mapping.market_alert_count = contact.market_alerts
        mapping.home_value_alert_count = contact.home_value_alerts
        mapping.last_imported_at = now
        report['rows'][index]['lead_id'] = lead.id
        report['rows'][index]['unsubscribed'] = bool(lead.is_unsubscribed)
    await db.flush()
    return report


async def database_report(contacts, apply):
    from app.preview import is_preview
    if apply and is_preview():
        raise ImportProblem('Applying imports is disabled in read-only preview mode.')
    from app.database import AsyncSessionLocal, engine
    try:
        bind = engine.execution_options(schema_translate_map={None: 'admin_demo'}) if (
            os.getenv('APP_ENV', '').strip().lower() == 'test'
        ) else engine
        async with AsyncSessionLocal(bind=bind) as db:
            async with db.begin():
                if not apply:
                    from sqlalchemy import text
                    await db.execute(text('SET TRANSACTION READ ONLY'))
                report = await import_contacts(db, contacts, apply)
                if report['conflicts'] and apply:
                    raise ImportProblem('Import blocked by database conflicts. Run --check-database to review the summary.')
            report['applied'] = apply
            return report
    finally:
        await engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_file', nargs='?', default='contacts.csv')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-database', action='store_true', help='Read-only comparison with configured database')
    mode.add_argument('--apply', action='store_true', help='Write contacts and source mappings atomically')
    parser.add_argument('--report', help='Write a new private JSON report with row numbers and internal client IDs; refuses overwrite')
    args = parser.parse_args(argv)
    contacts, issues = load_contacts(args.csv_file)
    report = offline_report(contacts, issues)
    if not report['errors'] and (args.check_database or args.apply):
        report = asyncio.run(database_report(contacts, args.apply))
        report['csv_warnings'] = [issue for issue in issues if issue.get('warning')]
    # Aggregate counts and row numbers only. No names, emails, phones, source IDs,
    # credentials, unsubscribe tokens, or raw exception messages are displayed.
    if args.report:
        try:
            descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as output:
                json.dump(report, output, indent=2)
        except OSError:
            state = 'Import was applied.' if report.get('applied') else 'No import was applied.'
            print(state + ' Report could not be written; choose a new writable report filename.', file=sys.stderr)
            return 1
    summary = {key: value for key, value in report.items() if key != 'rows'}
    print(json.dumps(summary, indent=2))
    return 1 if report.get('errors') or report.get('conflicts') else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ImportProblem as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print('Import failed; database work, if started, was rolled back unless the commit outcome is uncertain. Check configuration and migration 003; rerun --check-database before retrying. No emails sent. Details omitted to protect private data.', file=sys.stderr)
        raise SystemExit(1) from None
