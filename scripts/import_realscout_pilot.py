"""Import up to three reviewed contacts and their searches, always paused.

Default is offline validation. --check-database is read-only; --apply commits one
transaction. RealScout settings and existing unsubscribe preferences are untouched.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import replace
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, or_, text
from app.models import SavedSearch
from app.preview import is_preview
from app.routers.admin_searches import Criteria
from scripts.import_realscout_contacts import ImportProblem, load_contacts, import_contacts


class PilotSearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contact_id: str = Field(min_length=1, max_length=255)
    search_id: str = Field(min_length=1, max_length=255)
    criteria: Criteria
    frequency: Literal['instant', 'daily', 'weekly', 'monthly']
    review_required: Literal[True] = True
    review_reason: str = Field(default='Review source search before activation', max_length=500)
    blocked_reasons: list[str] = Field(default_factory=list)


class PilotManifest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1]
    contacts: list[str] = Field(min_length=1, max_length=3)
    searches: list[PilotSearch] = Field(default_factory=list, max_length=15)
    review_notes: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_batch(self):
        if len(set(self.contacts)) != len(self.contacts):
            raise ValueError('Duplicate contacts in pilot')
        if len({s.search_id for s in self.searches}) != len(self.searches):
            raise ValueError('Duplicate source searches')
        if any(s.contact_id not in self.contacts or s.blocked_reasons for s in self.searches):
            raise ValueError('Unreviewed or unassociated source search')
        return self


def load_batch(csv_path, manifest_path):
    try:
        manifest = PilotManifest.model_validate(json.loads(Path(manifest_path).read_text()))
    except Exception:
        raise ImportProblem('Pilot manifest is invalid or includes blocked source criteria.') from None
    contacts, issues = load_contacts(csv_path)
    if any(not issue.get('warning') for issue in issues):
        raise ImportProblem('CSV contains errors; run the offline contact validator first.')
    by_id = {c.contact_id: c for c in contacts}
    if any(source_id not in by_id for source_id in manifest.contacts):
        raise ImportProblem('A pilot contact does not match the supplied CSV.')
    phones = Counter(c.phone for c in contacts if c.phone)
    # Apply the shared-phone rule to the whole export, not only this small batch.
    selected = [replace(by_id[key], phone=None) if phones[by_id[key].phone] > 1 else by_id[key]
                for key in manifest.contacts]
    return selected, manifest


async def import_pilot(db, contacts, manifest, apply=False):
    if apply and is_preview():
        raise ImportProblem('Applying imports is disabled in read-only preview mode.')
    contact_report = await import_contacts(db, contacts, apply)
    if contact_report['conflicts']:
        raise ImportProblem('Contact conflicts block the pilot. Compare contacts before applying.')
    lead_ids = {c.contact_id: row['lead_id'] for c, row in zip(contacts, contact_report['rows'])}
    source_ids = [s.search_id for s in manifest.searches]
    existing = (await db.execute(select(SavedSearch).where(or_(
        SavedSearch.lead_id.in_([value for value in lead_ids.values() if value is not None]),
        SavedSearch.criteria['_realscout']['search_id'].as_string().in_(source_ids)
    )))).scalars().all()
    report = {'applied': False, 'contacts': len(contacts), 'new_clients': contact_report['new_clients'],
              'existing_clients': contact_report['existing_clients'], 'new_paused_searches': 0,
              'reused_paused_searches': 0, 'source_schedules': dict(Counter(s.frequency for s in manifest.searches)),
              'review_required': True, 'contact_rows': contact_report['rows'], 'rows': []}
    to_create = []
    for search in manifest.searches:
        lead_id = lead_ids[search.contact_id]
        criteria = search.criteria.filters()
        matches = [row for row in existing if isinstance(row.criteria, dict)
                   and isinstance(row.criteria.get('_realscout'), dict)
                   and row.criteria['_realscout'].get('search_id') == search.search_id]
        if matches:
            row = matches[0]
            existing_filters = {key: value for key, value in row.criteria.items() if key != '_realscout'}
            if len(matches) != 1 or row.lead_id != lead_id or existing_filters != criteria or row.frequency != search.frequency or row.is_active:
                raise ImportProblem('Source search conflicts with an existing or activated search. No pilot changes committed.')
            report['reused_paused_searches'] += 1
            report['rows'].append({'lead_id': lead_id, 'search_id': row.id, 'is_active': False})
            continue
        if lead_id is not None and any(row.lead_id == lead_id and row.criteria == criteria and row.frequency == search.frequency for row in existing):
            raise ImportProblem('A matching search already exists without source tracking. Review it before importing.')
        report['new_paused_searches'] += 1
        to_create.append((search, lead_id, criteria))
    if apply:
        for source, lead_id, criteria in to_create:
            row = SavedSearch(lead_id=lead_id, frequency=source.frequency, is_active=False,
                criteria={**criteria, '_realscout': {'search_id': source.search_id,
                    'frequency': source.frequency, 'review_required': True,
                    'review_reason': source.review_reason}})
            db.add(row)
            await db.flush()
            report['rows'].append({'lead_id': lead_id, 'search_id': row.id, 'is_active': False})
    return report


async def run_database(contacts, manifest, apply):
    if apply and is_preview():
        raise ImportProblem('Applying imports is disabled in read-only preview mode.')
    from app.database import AsyncSessionLocal, engine
    bind = engine.execution_options(schema_translate_map={None: 'admin_demo'}) if (
        os.getenv('APP_ENV', '').strip().lower() == 'test') else engine
    try:
        async with AsyncSessionLocal(bind=bind) as db:
            async with db.begin():
                if not apply:
                    await db.execute(text('SET TRANSACTION READ ONLY'))
                report = await import_pilot(db, contacts, manifest, apply)
            report['applied'] = apply
            return report
    finally:
        await engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--csv', default='contacts.csv')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-database', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--report', help='New private JSON receipt; will not overwrite an existing file')
    args = parser.parse_args(argv)
    if args.report and Path(args.report).exists():
        raise ImportProblem('Report already exists; choose a new filename before importing.')
    contacts, manifest = load_batch(args.csv, args.manifest)
    report = {'mode': 'offline_validation', 'contacts': len(contacts), 'searches': len(manifest.searches),
              'applied': False, 'review_required': True, 'source_schedules': dict(Counter(s.frequency for s in manifest.searches))}
    if args.check_database or args.apply:
        report = asyncio.run(run_database(contacts, manifest, args.apply))
    print(json.dumps({k: v for k, v in report.items() if k not in {'rows', 'contact_rows'}}, indent=2))
    if args.report:
        try:
            fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output: json.dump(report, output, indent=2)
        except OSError:
            raise ImportProblem('The result above is authoritative; the private report could not be saved.') from None
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ImportProblem as error:
        print(str(error))
        raise SystemExit(1) from None
    except Exception:
        print('Pilot failed; check database state before retrying if the commit outcome is uncertain. Details withheld to protect client data.')
        raise SystemExit(1) from None
