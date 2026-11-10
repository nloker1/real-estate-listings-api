"""Pure email rendering shared by the worker and offline previews."""
import re
from html import escape
from urllib.parse import quote

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

def digest_content(listings, frequency, unsubscribe_url):
    cards = []
    for listing in listings:
        url = ("https://gorgerealty.com/property/" + quote(create_slug(listing.address), safe="")
               + "/" + quote(str(listing.mls_number), safe=""))
        photo = listing.photo_url or ""
        from urllib.parse import urlsplit
        image = (f'<img src="{escape(photo, quote=True)}" alt="" style="max-width:100%;" />'
                 if urlsplit(photo).scheme in {"https", "http"} else "")
        facts = f"{listing.beds if listing.beds is not None else '—'} beds | {listing.baths if listing.baths is not None else '—'} baths"
        cards.append(f'<section style="padding:16px 0;border-bottom:1px solid #ddd;">{image}'
                     f'<h2>{escape(listing.address or "Property")}</h2>'
                     f'<p>${listing.price:,}</p><p>{escape(facts)}</p>'
                     f'<a href="{escape(url, quote=True)}">View listing details</a></section>')
    title = f"Your {frequency} home matches: {len(listings)} new listings"
    html = ('<div style="font-family:sans-serif;max-width:600px;margin:auto;">'
            f'<h1>{escape(title)}</h1>' + "".join(cards)
            + '<p>Gorge Realty | White Salmon, WA 98672</p>'
            + f'<p><a href="{escape(unsubscribe_url, quote=True)}">Unsubscribe</a> from these alerts</p></div>')
    return title, html


