# src/tracker/ingest/domain_scraper.py
"""Scrape Domain.com.au sold listings via headless Chromium (Playwright).

Domain blocks plain HTTP requests. We use a real headless browser to load the
page, then extract listing data from the embedded __NEXT_DATA__ JSON or
JSON-LD structured data.
"""

import json
import logging
import re
import time
import hashlib
from typing import List, Optional

from bs4 import BeautifulSoup

from tracker.ingest.normalise import normalise_address

logger = logging.getLogger(__name__)

PROPERTY_TYPE_MAP = {
    'apartment': 'unit',
    'apartmentunitflat': 'unit',
    'unit': 'unit',
    'studio': 'unit',
    'house': 'house',
    'townhouse': 'house',
    'villa': 'house',
    'duplex': 'house',
    'terrace': 'house',
    'semi-detached': 'house',
    'semidetached': 'house',
}


def build_sold_listings_url(suburb: str, postcode: str, property_type: str) -> str:
    """Build Domain.com.au sold listings URL for a suburb."""
    postcode = str(int(float(postcode))) if postcode else ''
    slug = f"{suburb.lower().replace(' ', '-')}-nsw-{postcode}"
    ptype = 'apartment' if property_type == 'unit' else 'house'
    return f"https://www.domain.com.au/sold-listings/{slug}/?ptype={ptype}&sort=dateupdated-desc"


def _parse_listing_from_card(card_data: dict, suburb: str, postcode: str) -> Optional[dict]:
    """Parse a listing from extracted card data into provisional_sales format."""
    address = card_data.get('address', '')
    price = card_data.get('price')
    sold_date = card_data.get('sold_date', '')
    listing_url = card_data.get('url', '')

    if not address or not price:
        return None

    unit_number = None
    house_number = None
    street_name = None

    # Try unit format: "9/27-29 Morton Street"
    unit_match = re.match(r'^(\d+[a-zA-Z]?)\s*/\s*(\d+(?:-\d+)?[a-zA-Z]?)\s+(.+)$', address)
    if unit_match:
        unit_number = unit_match.group(1)
        house_number = unit_match.group(2)
        street_name = unit_match.group(3).strip()
    else:
        # Try house format: "15 Alliance Avenue"
        house_match = re.match(r'^(\d+(?:-\d+)?[a-zA-Z]?)\s+(.+)$', address)
        if house_match:
            house_number = house_match.group(1)
            street_name = house_match.group(2).strip()

    if not house_number:
        return None

    bedrooms = card_data.get('bedrooms')
    bathrooms = card_data.get('bathrooms')
    car_spaces = card_data.get('car_spaces')

    address_normalised = normalise_address(
        unit_number=unit_number,
        house_number=house_number,
        street_name=street_name or '',
        suburb=suburb,
        postcode=postcode,
    )

    digest = hashlib.sha256(address_normalised.encode('utf-8')).hexdigest()[:20]
    sale_id = f"domain-scrape-{digest}"

    return {
        'id': sale_id,
        'source': 'domain-scrape',
        'unit_number': unit_number,
        'house_number': house_number,
        'street_name': street_name,
        'suburb': suburb,
        'postcode': postcode,
        'property_type': card_data.get('property_type', 'other'),
        'sold_price': int(price),
        'sold_date': sold_date,
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'car_spaces': car_spaces,
        'address_normalised': address_normalised,
        'listing_url': listing_url,
        'source_site': 'domain.com.au',
        'status': 'unconfirmed',
        'raw_json': json.dumps(card_data),
    }


def _extract_next_data(html: str) -> Optional[dict]:
    """Extract __NEXT_DATA__ JSON from the HTML page."""
    soup = BeautifulSoup(html, 'html.parser')
    script = soup.find('script', id='__NEXT_DATA__')
    if script and script.string:
        try:
            return json.loads(script.string)
        except json.JSONDecodeError:
            pass

    # Fallback: regex search for the script tag
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None


def _parse_next_data(data: dict) -> List[dict]:
    """Parse listings from Next.js __NEXT_DATA__ JSON blob."""
    listings = []
    try:
        props = data.get('props', {}).get('pageProps', {})

        # Try common keys for listing data
        for key in ['listingsMap', 'listings', 'soldListings', 'results',
                     'data', 'componentProps']:
            items = props.get(key)
            if isinstance(items, list):
                for item in items:
                    parsed = _normalize_next_listing(item)
                    if parsed:
                        listings.append(parsed)
            elif isinstance(items, dict):
                for sub_key in ['listings', 'results', 'items']:
                    sub_items = items.get(sub_key)
                    if isinstance(sub_items, list):
                        for item in sub_items:
                            parsed = _normalize_next_listing(item)
                            if parsed:
                                listings.append(parsed)

        # Also try deeply nested structures common in Domain
        if not listings:
            listings = _deep_search_listings(props)

    except Exception as e:
        logger.debug(f"Failed to parse __NEXT_DATA__: {e}")

    return listings


def _deep_search_listings(data, depth=0) -> List[dict]:
    """Recursively search for listing-like objects in nested data."""
    if depth > 5:
        return []

    listings = []
    if isinstance(data, dict):
        # Check if this dict looks like a listing
        if 'listingSlug' in data or ('propertyDetails' in data) or \
           ('saleDetails' in data and 'propertyDetails' in data.get('listing', {})):
            parsed = _normalize_next_listing(data)
            if parsed:
                listings.append(parsed)
        else:
            for value in data.values():
                listings.extend(_deep_search_listings(value, depth + 1))
    elif isinstance(data, list):
        for item in data:
            listings.extend(_deep_search_listings(item, depth + 1))

    return listings


def _normalize_next_listing(item: dict) -> Optional[dict]:
    """Normalize a listing from __NEXT_DATA__ into our standard format."""
    if not isinstance(item, dict):
        return None

    listing = item.get('listing', item)
    props = listing.get('propertyDetails', listing)

    address_parts = []
    unit = props.get('unitNumber', '')
    street_num = props.get('streetNumber', '')
    street = props.get('street', '') or props.get('streetName', '')

    if unit and street_num:
        address_parts.append(f"{unit}/{street_num}")
    elif street_num:
        address_parts.append(street_num)

    if street:
        address_parts.append(street)

    address = ' '.join(address_parts)

    # Price
    sale_details = listing.get('saleDetails', {})
    price = sale_details.get('soldPrice') or listing.get('price')

    # Sold date
    sold_date = sale_details.get('soldDate', '') or listing.get('soldDate', '')
    if sold_date and 'T' in sold_date:
        sold_date = sold_date.split('T')[0]

    # URL
    slug = listing.get('listingSlug', '')
    url = f"https://www.domain.com.au/{slug}" if slug else ''

    # Property type
    ptype = props.get('propertyType', '').lower()

    return {
        'address': address,
        'price': price,
        'sold_date': sold_date,
        'url': url,
        'bedrooms': props.get('bedrooms'),
        'bathrooms': props.get('bathrooms'),
        'car_spaces': props.get('carSpaces') or props.get('carspaces'),
        'property_type': PROPERTY_TYPE_MAP.get(ptype, 'other'),
    }


def _extract_json_ld(html: str) -> List[dict]:
    """Extract JSON-LD structured data from HTML."""
    listings = []
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
            listings.extend(_parse_json_ld(data))
        except (json.JSONDecodeError, TypeError):
            pass
    return listings


def _parse_json_ld(data) -> List[dict]:
    """Parse JSON-LD structured data for real estate listings."""
    listings = []
    if isinstance(data, list):
        for item in data:
            listings.extend(_parse_json_ld(item))
    elif isinstance(data, dict):
        if data.get('@type') in ('Product', 'RealEstateListing', 'Residence'):
            address = data.get('name', '') or data.get('address', '')
            if isinstance(address, dict):
                address = address.get('streetAddress', '')
            price = data.get('offers', {}).get('price')
            if address:
                listings.append({
                    'address': address,
                    'price': price,
                    'sold_date': '',
                    'url': data.get('url', ''),
                    'bedrooms': None,
                    'bathrooms': None,
                    'car_spaces': None,
                    'property_type': 'other',
                })
    return listings


def _launch_browser():
    """Launch a stealth headless Firefox browser via Playwright.

    Firefox has a different TLS fingerprint from Chromium, which helps
    bypass CDN-level bot detection (e.g. Akamai) that blocks Chromium.

    Returns (playwright_instance, browser, page) tuple.
    Caller must close browser and stop playwright when done.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()

    # Use Firefox — Domain's CDN blocks Chromium's TLS/HTTP2 fingerprint
    # with ERR_HTTP2_PROTOCOL_ERROR. Firefox has a distinct fingerprint
    # that is less commonly blocked.
    browser = pw.firefox.launch(headless=True)
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        locale='en-AU',
        timezone_id='Australia/Sydney',
    )
    page = context.new_page()

    return pw, browser, page


def fetch_sold_listings_scrape(
    suburb: str,
    property_type: str,
    postcode: str,
) -> List[dict]:
    """Fetch sold listings by scraping Domain.com.au with headless Chromium.

    Uses Playwright to load the page in a real browser, then extracts listing
    data from __NEXT_DATA__ JSON or page HTML.

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        postcode: 4-digit postcode

    Returns:
        List of parsed provisional sale records.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        logger.error(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )
        return []

    url = build_sold_listings_url(suburb, postcode, property_type)
    logger.info(f"Scraping Domain sold listings: {url}")

    results = []
    pw = None
    browser = None

    try:
        # Rate limit between requests
        time.sleep(2.0)

        pw, browser, page = _launch_browser()

        # Navigate and wait for content
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        # Wait for listing cards to render
        page.wait_for_timeout(4000)

        # Extract listing data directly from rendered DOM via JavaScript.
        # This is more robust than parsing __NEXT_DATA__ JSON which changes
        # structure across Domain deploys.
        raw_listings = page.evaluate("""() => {
            const cards = document.querySelectorAll(
                '[data-testid*="listing-card"], '
                + '[class*="listing-result"], '
                + '[class*="ListingCard"], '
                + 'a[href*="/sold/"]'  // fallback: any sold listing link
            );

            // If no cards found via selectors, try broader approach:
            // look for any element with an address + price pattern
            const results = [];
            const seen = new Set();

            for (const card of cards) {
                const text = card.innerText || '';
                const href = card.href || card.querySelector('a')?.href || '';

                // Skip if no useful link
                if (!href.includes('domain.com.au/')) continue;
                if (seen.has(href)) continue;
                seen.add(href);

                // Extract price: look for $X,XXX,XXX or $X.Xm patterns
                let price = null;
                const priceMatch = text.match(
                    /\\$(\\d{1,3}(?:,\\d{3})+)/
                ) || text.match(
                    /\\$(\\d+\\.\\d+)\\s*[mM]/
                );
                if (priceMatch) {
                    const raw = priceMatch[0].replace('$', '').replace(/,/g, '');
                    if (raw.toLowerCase().includes('m')) {
                        price = Math.round(parseFloat(raw) * 1000000);
                    } else {
                        price = parseInt(raw, 10);
                    }
                }

                // Extract sold date: "Sold DD Mon YYYY" or "Sold on DD Mon YYYY"
                let soldDate = '';
                const dateMatch = text.match(
                    /[Ss]old\\s+(?:on\\s+)?(\\d{1,2})\\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+(\\d{4})/i
                );
                if (dateMatch) {
                    const months = {jan:'01',feb:'02',mar:'03',apr:'04',
                        may:'05',jun:'06',jul:'07',aug:'08',
                        sep:'09',oct:'10',nov:'11',dec:'12'};
                    const d = dateMatch[1].padStart(2, '0');
                    const m = months[dateMatch[2].toLowerCase()] || '01';
                    soldDate = dateMatch[3] + '-' + m + '-' + d;
                }

                // Extract bed/bath/car from text like "3 Beds 2 Baths 1 Car"
                const beds = text.match(/(\\d+)\\s*[Bb]ed/);
                const baths = text.match(/(\\d+)\\s*[Bb]ath/);
                const cars = text.match(/(\\d+)\\s*[Cc]ar/);

                // Extract address from rendered card text.
                // Domain cards typically show address as "15 Alliance Avenue"
                // or "9/27-29 Morton Street" on the first line or in a
                // specific element.
                let address = '';

                // Try specific address elements first
                const addrEl = card.querySelector(
                    '[data-testid*="address"], '
                    + '[class*="address-line1"], '
                    + '[class*="Address"], '
                    + 'h2'
                );
                if (addrEl) {
                    // Take first line only (address line 1, not suburb)
                    const addrText = addrEl.textContent.trim();
                    const firstLine = addrText.split('\\n')[0].trim();
                    // Only use if it starts with a number (looks like street address)
                    if (/^\\d/.test(firstLine)) {
                        address = firstLine;
                    }
                }

                // Fallback: first line of card text that looks like an address
                if (!address) {
                    const lines = text.split('\\n')
                        .map(l => l.trim())
                        .filter(l => l.length > 0);
                    for (const line of lines) {
                        // Match "15 Alliance Avenue" or "9/27-29 Morton St"
                        if (/^\\d+[a-zA-Z]?\\s*[\\/]?\\s*\\d*/.test(line) &&
                            /[A-Za-z]/.test(line) &&
                            line.length < 80) {
                            address = line;
                            break;
                        }
                    }
                }

                results.push({
                    address: address,
                    price: price,
                    sold_date: soldDate,
                    url: href,
                    bedrooms: beds ? parseInt(beds[1]) : null,
                    bathrooms: baths ? parseInt(baths[1]) : null,
                    car_spaces: cars ? parseInt(cars[1]) : null,
                    raw_text: text.substring(0, 300),
                });
            }
            return results;
        }""")

        logger.info(
            f"DOM extraction: {len(raw_listings)} listing elements for {suburb}"
        )

        # If DOM extraction found nothing, fall back to __NEXT_DATA__
        if not raw_listings:
            try:
                next_data = page.evaluate('() => window.__NEXT_DATA__')
                if next_data and isinstance(next_data, dict):
                    raw_listings = _parse_next_data(next_data)
                    if raw_listings:
                        logger.info(
                            f"Fallback: {len(raw_listings)} from __NEXT_DATA__"
                        )
            except Exception:
                pass

        # If still nothing, try JSON-LD from HTML
        if not raw_listings:
            html = page.content()
            raw_listings = _extract_json_ld(html)
            if raw_listings:
                logger.info(f"Fallback: {len(raw_listings)} from JSON-LD")

        # Log first result for debugging
        if raw_listings:
            s = raw_listings[0]
            logger.info(
                f"Sample: address='{s.get('address', '')[:60]}', "
                f"price={s.get('price')}, "
                f"date='{s.get('sold_date', '')}', "
                f"text='{s.get('raw_text', '')[:80]}'"
            )

        # Convert to provisional_sales format
        for listing_data in raw_listings:
            # For DOM-extracted listings, parse address from raw_text
            # if the URL-based address didn't work
            if not listing_data.get('address') and listing_data.get('raw_text'):
                # Try to extract address from the card text
                raw = listing_data['raw_text']
                addr_match = re.match(
                    r'^(\d+[a-zA-Z]?\s*/\s*)?(\d+(?:-\d+)?[a-zA-Z]?)\s+(.+?)(?:\s*,|\n)',
                    raw,
                )
                if addr_match:
                    parts = []
                    if addr_match.group(1):
                        parts.append(addr_match.group(1).strip())
                    parts.append(addr_match.group(2))
                    parts.append(addr_match.group(3).strip())
                    listing_data['address'] = ' '.join(parts)

            # Remove raw_text before passing to parser
            listing_data.pop('raw_text', None)
            listing_data['property_type'] = property_type

            parsed = _parse_listing_from_card(listing_data, suburb, postcode)
            if parsed:
                results.append(parsed)

        logger.info(f"Domain scrape: {len(results)} sold {property_type}s in {suburb}")

    except Exception as e:
        logger.error(f"Domain scrape failed for {suburb}: {e}")

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    return results
