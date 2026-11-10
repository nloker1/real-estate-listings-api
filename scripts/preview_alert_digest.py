"""Render a fictional digest locally. No environment, database, or email transport."""
import argparse
import base64
from html import escape
from pathlib import Path
from tempfile import gettempdir
from types import SimpleNamespace

from app.services.alert_email import digest_content


def preview_html(frequency):
    listings = [SimpleNamespace(address=address, price=price, beds=beds, baths=baths,
                  mls_number=f'SYNTHETIC-PREVIEW-{index}',
                  photo_url=f'https://preview.invalid/house-{index}.svg')
                for index, (address, price, beds, baths) in enumerate([
                    ('101 Fictional Gorge Lane', 625000, 3, 2),
                    ('202 Example Orchard Road', 749000, 4, 2.5),
                    ('303 Synthetic Mountain Drive', 895000, 3, 3),
                ], 1)]
    subject, body = digest_content(listings, frequency, '#preview-unsubscribe')
    for index, listing in enumerate(listings, 1):
        # Embedded illustrations keep the preview entirely offline.
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="560" height="250" viewBox="0 0 560 250">'
               '<rect width="560" height="250" fill="#e5edf0"/>'
               '<path d="M0 180L130 55L260 180L400 35L560 180V250H0" fill="#a5bcb4"/>'
               '<path d="M200 145L280 85L360 145Z" fill="#345367"/>'
               '<rect x="215" y="145" width="130" height="75" fill="#faf7ee"/>'
               '<rect x="265" y="165" width="30" height="55" fill="#345367"/>'
               f'<text x="15" y="235" font-family="sans-serif" font-size="14">Fictional preview home {index}</text></svg>')
        data = 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()
        body = body.replace(listing.photo_url, data)
    # Neutralize property links in this preview; production uses the real URLs.
    import re
    body = re.sub(r'href="https://gorgerealty.com/property/[^\"]+"', 'href="#preview-listing"', body)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>{escape(subject)}</title></head><body>'
            '<aside style="font-family:sans-serif;padding:16px;background:#fff3cd;">'
            f'<strong>PREVIEW — fictional data, no email sent</strong><p>Subject: {escape(subject)}</p>'
            '<p>Images are offline placeholders. Links are inactive preview anchors.</p></aside>'
            + body + '</body></html>')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frequency', choices=['weekly', 'monthly'], default='weekly')
    parser.add_argument('--output', type=Path, help='HTML output path (default: system temporary directory)')
    args = parser.parse_args(argv)
    target = args.output or Path(gettempdir()) / f'gorge-{args.frequency}-digest-preview.html'
    target.write_text(preview_html(args.frequency), encoding='utf-8')
    print(f'Preview saved to {target.resolve()}')
    print('Fictional listings only. No database accessed or email sent.')


if __name__ == '__main__':
    main()
