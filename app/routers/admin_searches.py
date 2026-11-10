"""Authenticated saved-search editing; no email delivery is performed here."""
import hmac
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, func
from app.admin_auth import require_admin, origin_check
from app.models import SavedSearch
from app.preview import is_preview
from app.services.admin_reporting import require_client, page_result
from app.services.search_matching import listing_matches, editable_search

router = APIRouter(dependencies=[Depends(require_admin)])


class Criteria(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    cities: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=50)
    zipcodes: list[Annotated[str, Field(pattern=r'^\d{5}$')]] = Field(default_factory=list, max_length=50)
    minPrice: int | None = Field(None, ge=0, le=1000000000)
    maxPrice: int | None = Field(None, ge=0, le=1000000000)
    minBeds: int | None = Field(None, ge=0, le=100)
    minBaths: float | None = Field(None, ge=0, le=100)
    minSqft: int | None = Field(None, ge=0, le=100000000)
    maxSqft: int | None = Field(None, ge=0, le=100000000)
    minAcres: float | None = Field(None, ge=0, le=10000000)
    maxAcres: float | None = Field(None, ge=0, le=10000000)
    propertyType: str | None = Field(None, max_length=100)

    @model_validator(mode='after')
    def validate_filters(self):
        self.zipcodes = list(dict.fromkeys(self.zipcodes))
        self.cities = list(dict.fromkeys(city.strip() for city in self.cities))
        if any(not city or '%' in city or '_' in city for city in self.cities):
            raise ValueError('Enter city names without wildcard characters')
        for low, high in [('minPrice', 'maxPrice'), ('minSqft', 'maxSqft'), ('minAcres', 'maxAcres')]:
            if getattr(self, low) is not None and getattr(self, high) is not None and getattr(self, low) > getattr(self, high):
                raise ValueError('Minimum must not exceed maximum')
        if self.propertyType is not None:
            self.propertyType = self.propertyType.strip() or None
        return self

    def filters(self):
        return self.model_dump(exclude_none=True)


class SearchBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    criteria: Criteria
    frequency: Literal["instant", "daily", "weekly", "monthly"] = "instant"


class SearchState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_active: bool


def trusted_request(request: Request, admin=Depends(require_admin)):
    origin_check(request)
    if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), admin['csrf'].encode()):
        raise HTTPException(403, 'Invalid request token')


def writable(request: Request, _=Depends(trusted_request)):
    if is_preview():
        raise HTTPException(403, 'Read-only preview: saving changes is disabled')



async def owned_search(db, client_id, search_id):
    search = (await db.execute(select(SavedSearch).where(
        SavedSearch.id == search_id, SavedSearch.lead_id == client_id))).scalar_one_or_none()
    if search is None:
        raise HTTPException(404, 'Search not found for this client')
    return search


def configure_routes(admin_db):
    @router.post('/clients/{client_id}/search-preview', dependencies=[Depends(trusted_request)])
    async def preview(client_id: int, body: SearchBody, page: int = Query(1, ge=1, le=100000),
                      page_size: int = Query(20, ge=1, le=100), db=Depends(admin_db)):
        await require_client(db, client_id)
        query = listing_matches(body.criteria.filters())
        total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        from app.models import Listing
        listings = (await db.execute(query.order_by(Listing.created_at.desc(), Listing.id.desc())
            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return {**page_result([{'id': row.id, 'mls_number': row.mls_number, 'address': row.address,
                 'city': row.city, 'price': row.price, 'beds': row.beds, 'baths': row.baths}
                 for row in listings], total, page, page_size),
                'note': 'Current active, published matches. Alerts include only listings added after search creation and exclude prior successful sends.'}

    @router.post('/clients/{client_id}/searches', status_code=201, dependencies=[Depends(writable)])
    async def create(client_id: int, body: SearchBody, db=Depends(admin_db)):
        await require_client(db, client_id)
        search = SavedSearch(lead_id=client_id, criteria=body.criteria.filters(), frequency=body.frequency, is_active=False)
        db.add(search)
        await db.commit()
        return {'id': search.id, 'is_active': False}

    @router.put('/clients/{client_id}/searches/{search_id}', dependencies=[Depends(writable)])
    async def edit(client_id: int, search_id: int, body: SearchBody, db=Depends(admin_db)):
        search = await owned_search(db, client_id, search_id)
        if not editable_search(search):
            raise HTTPException(409, 'This search has settings the editor does not support')
        search.criteria = body.criteria.filters()
        search.frequency = body.frequency
        # A criteria change always requires review and explicit reactivation.
        search.is_active = False
        await db.commit()
        return {'id': search.id, 'is_active': False}

    @router.patch('/clients/{client_id}/searches/{search_id}/state', dependencies=[Depends(writable)])
    async def state(client_id: int, search_id: int, body: SearchState, db=Depends(admin_db)):
        lead = await require_client(db, client_id)
        search = await owned_search(db, client_id, search_id)
        if body.is_active and lead.is_unsubscribed:
            raise HTTPException(409, 'This client is unsubscribed; their preferences have not been changed')
        if body.is_active and not editable_search(search):
            raise HTTPException(409, 'Review unsupported search settings before activation')
        if body.is_active:
            try:
                Criteria.model_validate(search.criteria)
            except ValidationError:
                raise HTTPException(409, 'Review and correct the listing filters before activation') from None
        search.is_active = body.is_active
        await db.commit()
        return {'id': search.id, 'is_active': search.is_active}
