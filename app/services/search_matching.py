"""Listing filters shared by saved-search preview and email matching."""
from sqlalchemy import select, or_, func
from app.models import Listing

FILTER_KEYS = {'cities', 'minPrice', 'maxPrice', 'minBeds', 'minBaths', 'propertyType',
               'minSqft', 'maxSqft', 'minAcres', 'maxAcres'}


def editable_search(search):
    return (search.frequency == 'instant' and isinstance(search.criteria, dict)
            and not (set(search.criteria) - FILTER_KEYS))


def listing_matches(criteria):
    query = select(Listing).where(Listing.is_published == True, Listing.status == 'Active')
    for key, column, operator, convert in [
        ('minPrice', Listing.price, 'min', int), ('maxPrice', Listing.price, 'max', int),
        ('minBeds', Listing.beds, 'min', int), ('minBaths', Listing.baths, 'min', float),
        ('minSqft', Listing.sqft, 'min', int), ('maxSqft', Listing.sqft, 'max', int),
        ('minAcres', Listing.acreage, 'min', float), ('maxAcres', Listing.acreage, 'max', float),
    ]:
        if criteria.get(key) is not None and criteria.get(key) != '':
            value = convert(criteria[key])
            query = query.where(column >= value if operator == 'min' else column <= value)
    if criteria.get('propertyType'):
        query = query.where(Listing.property_type == criteria['propertyType'])
    if criteria.get('cities'):
        filters = []
        for city in criteria['cities']:
            compact = city.replace(' ', '')
            filters.extend([Listing.city.ilike(f'%{city}%'), Listing.city.ilike(f'%{compact}%'),
                            func.replace(Listing.city, ' ', '').ilike(f'%{compact}%')])
        query = query.where(or_(*filters))
    return query
