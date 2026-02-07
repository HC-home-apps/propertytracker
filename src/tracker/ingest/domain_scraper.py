# src/tracker/ingest/domain_scraper.py
"""Scrape Domain.com.au sold listings directly using headless browser.

Uses Playwright to render Domain's JS-heavy sold listings page,
then extracts structured data from the rendered DOM or embedded JSON.
"""

import json
import logging
import re
from typing import List, Optional

from tracker.ingest.normalise import normalise_address

logger = logging.getLogger(__name__)

PROPERTY_TYPE_MAP = {
    'apartment': 'unit',
    'unit': 'unit',
    'studio': 'unit',
    'house': 'house',
    'townhouse': 'house',
    'villa': 'house',
    'duplex': 'house',
    'terrace': 'house',
    'semi-detached': 'house',
}


def build_sold_listings_url(suburb: str, postcode: str, property_type: str) -> str:
    """Build Domain.com.au sold listings URL for a suburb."""
    # Ensure postcode is a clean string (DB may return float like 2212.0)
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

    # Parse address: "9/27-29 Morton Street" or "15 Alliance Avenue"
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

    # Parse bedrooms/bathrooms/car from card data
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

    # Generate a stable ID from the address
    addr_hash = abs(hash(address_normalised))
    sale_id = f"domain-scrape-{addr_hash}"

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


def _extract_listings_from_page(page) -> List[dict]:
    """Extract listing data from the rendered Domain sold listings page.

    Tries multiple strategies:
    1. Look for __NEXT_DATA__ or similar JSON blobs in script tags
    2. Parse DOM property cards directly
    """
    listings = []

    # Strategy 1: Extract from __NEXT_DATA__ (Next.js data)
    try:
        next_data = page.evaluate("""
            () => {
                const el = document.querySelector('script#__NEXT_DATA__');
                if (el) return JSON.parse(el.textContent);
                return null;
            }
        """)
        if next_data:
            listings = _parse_next_data(next_data)
            if listings:
                logger.info(f"Extracted {len(listings)} listings from __NEXT_DATA__")
                return listings
    except Exception as e:
        logger.debug(f"__NEXT_DATA__ extraction failed: {e}")

    # Strategy 2: Parse property cards from DOM
    try:
        cards = page.evaluate("""
            () => {
                const results = [];
                // Domain uses data-testid attributes on listing cards
                const cards = document.querySelectorAll(
                    '[data-testid*="listing-card"], [class*="listing-result"], li[class*="is-sold"]'
                );
                for (const card of cards) {
                    const data = {};

                    // Address: look for address element
                    const addrEl = card.querySelector(
                        '[data-testid*="address"], [class*="address"], h2 a, .listing-result__address'
                    );
                    if (addrEl) data.address = addrEl.textContent.trim();

                    // Price
                    const priceEl = card.querySelector(
                        '[data-testid*="price"], [class*="price"], .listing-result__price'
                    );
                    if (priceEl) data.price_text = priceEl.textContent.trim();

                    // Listing URL
                    const linkEl = card.querySelector('a[href*="/sold/"]') || card.querySelector('a[href]');
                    if (linkEl) data.url = linkEl.href;

                    // Features (beds/baths/car)
                    const featureEls = card.querySelectorAll(
                        '[data-testid*="property-features"] span, .property-feature'
                    );
                    const features = [];
                    featureEls.forEach(el => features.push(el.textContent.trim()));
                    data.features = features;

                    if (data.address) results.push(data);
                }
                return results;
            }
        """)

        for card in cards:
            listing = _parse_dom_card(card)
            if listing:
                listings.append(listing)

        if listings:
            logger.info(f"Extracted {len(listings)} listings from DOM cards")

    except Exception as e:
        logger.debug(f"DOM card extraction failed: {e}")

    # Strategy 3: Find any JSON-LD structured data
    try:
        json_ld = page.evaluate("""
            () => {
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                const data = [];
                scripts.forEach(s => {
                    try { data.push(JSON.parse(s.textContent)); } catch(e) {}
                });
                return data;
            }
        """)
        for item in json_ld:
            parsed = _parse_json_ld(item)
            listings.extend(parsed)

        if listings:
            logger.info(f"Extracted {len(listings)} from JSON-LD")

    except Exception as e:
        logger.debug(f"JSON-LD extraction failed: {e}")

    return listings


def _parse_next_data(data: dict) -> List[dict]:
    """Parse listings from Next.js __NEXT_DATA__ JSON blob."""
    listings = []
    try:
        # Navigate the Next.js data structure to find listing data
        # The exact path varies, so try common patterns
        props = data.get('props', {}).get('pageProps', {})

        # Try common patterns for listing data
        for key in ['listingsMap', 'listings', 'soldListings', 'results', 'data']:
            items = props.get(key)
            if isinstance(items, list):
                for item in items:
                    listings.append(_normalize_next_listing(item))
            elif isinstance(items, dict):
                # Sometimes listings are in a nested structure
                for sub_key in ['listings', 'results', 'items']:
                    sub_items = items.get(sub_key)
                    if isinstance(sub_items, list):
                        for item in sub_items:
                            listings.append(_normalize_next_listing(item))
    except Exception as e:
        logger.debug(f"Failed to parse __NEXT_DATA__: {e}")

    return [l for l in listings if l]


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


def _parse_dom_card(card: dict) -> Optional[dict]:
    """Parse a single DOM card extract into a listing dict."""
    address = card.get('address', '')
    price_text = card.get('price_text', '')

    # Strip suburb/state/postcode from address
    # e.g. "9/27-29 Morton Street, Wollstonecraft NSW 2065" → "9/27-29 Morton Street"
    address = re.sub(r',\s*\w[\w\s]*(?:NSW|VIC|QLD)\s*\d{0,4}.*$', '', address).strip()

    # Parse price from text like "Sold for $1,200,000" or "$1.2m"
    price = None
    m_match = re.search(r'\$(\d+(?:\.\d+)?)\s*[mM]', price_text)
    if m_match:
        price = int(float(m_match.group(1)) * 1_000_000)
    else:
        full_match = re.search(r'\$([\d,]+)', price_text)
        if full_match:
            try:
                price = int(full_match.group(1).replace(',', ''))
            except ValueError:
                pass

    # Parse features
    features = card.get('features', [])
    bedrooms = bathrooms = car_spaces = None
    for feat in features:
        bed_m = re.search(r'(\d+)\s*bed', feat, re.IGNORECASE)
        if bed_m:
            bedrooms = int(bed_m.group(1))
        bath_m = re.search(r'(\d+)\s*bath', feat, re.IGNORECASE)
        if bath_m:
            bathrooms = int(bath_m.group(1))
        car_m = re.search(r'(\d+)\s*car', feat, re.IGNORECASE)
        if car_m:
            car_spaces = int(car_m.group(1))

    if not address or not price:
        return None

    return {
        'address': address,
        'price': price,
        'sold_date': '',
        'url': card.get('url', ''),
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'car_spaces': car_spaces,
        'property_type': 'other',  # Will be filtered by caller
    }


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


def fetch_sold_listings_scrape(
    suburb: str,
    property_type: str,
    postcode: str,
) -> List[dict]:
    """Fetch sold listings by scraping Domain.com.au directly with Playwright.

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        postcode: 4-digit postcode

    Returns:
        List of parsed provisional sale records.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping Domain scrape")
        return []

    url = build_sold_listings_url(suburb, postcode, property_type)
    logger.info(f"Scraping Domain sold listings: {url}")

    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-http2',
                ],
            )
            context = browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (X11; Linux x86_64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/131.0.0.0 Safari/537.36'
                ),
                viewport={'width': 1280, 'height': 800},
                ignore_https_errors=True,
            )
            page = context.new_page()

            # Navigate — use domcontentloaded since networkidle can hang
            page.goto(url, wait_until='domcontentloaded', timeout=30000)

            # Wait for dynamic content to render
            page.wait_for_timeout(5000)

            # Extract listings
            raw_listings = _extract_listings_from_page(page)

            browser.close()

        # Convert to provisional_sales format
        for listing_data in raw_listings:
            parsed = _parse_listing_from_card(listing_data, suburb, postcode)
            if parsed and parsed['property_type'] == property_type:
                results.append(parsed)
            elif parsed and listing_data.get('property_type') == 'other':
                # If property type couldn't be determined, include it
                # (caller filtered by property type already via URL param)
                parsed['property_type'] = property_type
                results.append(parsed)

        logger.info(f"Scraped {len(results)} sold {property_type}s in {suburb} from Domain")

    except Exception as e:
        logger.error(f"Domain scrape failed for {suburb}: {e}")

    return results
