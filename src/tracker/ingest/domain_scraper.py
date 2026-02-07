# src/tracker/ingest/domain_scraper.py
"""Scrape Domain.com.au sold listings via server-rendered HTML.

Domain uses Next.js which embeds listing data in a __NEXT_DATA__ script tag.
We fetch the raw HTML with requests and extract the JSON — no browser needed.
"""

import json
import logging
import re
import time
from typing import List, Optional

import requests
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

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/131.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-AU,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
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


def fetch_sold_listings_scrape(
    suburb: str,
    property_type: str,
    postcode: str,
) -> List[dict]:
    """Fetch sold listings by scraping Domain.com.au sold listings page.

    Uses plain HTTP requests to fetch the HTML and extracts listing data
    from the embedded __NEXT_DATA__ JSON or JSON-LD structured data.

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        postcode: 4-digit postcode

    Returns:
        List of parsed provisional sale records.
    """
    url = build_sold_listings_url(suburb, postcode, property_type)
    logger.info(f"Fetching Domain sold listings: {url}")

    results = []

    try:
        # Rate limit: 1 second between requests
        time.sleep(1.0)

        response = requests.get(url, headers=HEADERS, timeout=30)

        if response.status_code != 200:
            logger.warning(f"Domain returned HTTP {response.status_code} for {suburb}")
            logger.debug(f"Response: {response.text[:300]}")
            return []

        html = response.text
        logger.info(f"Fetched {len(html)} bytes from Domain for {suburb}")

        # Strategy 1: Extract from __NEXT_DATA__
        raw_listings = []
        next_data = _extract_next_data(html)
        if next_data:
            raw_listings = _parse_next_data(next_data)
            if raw_listings:
                logger.info(f"Extracted {len(raw_listings)} listings from __NEXT_DATA__")

        # Strategy 2: JSON-LD fallback
        if not raw_listings:
            raw_listings = _extract_json_ld(html)
            if raw_listings:
                logger.info(f"Extracted {len(raw_listings)} listings from JSON-LD")

        if not raw_listings:
            logger.warning(f"No listings found in Domain HTML for {suburb} ({len(html)} bytes)")
            # Log a snippet of the HTML for debugging
            if '__NEXT_DATA__' in html:
                logger.debug("__NEXT_DATA__ tag found but parsing failed")
            else:
                logger.debug("No __NEXT_DATA__ tag in HTML")

        # Convert to provisional_sales format
        for listing_data in raw_listings:
            parsed = _parse_listing_from_card(listing_data, suburb, postcode)
            if parsed and parsed['property_type'] == property_type:
                results.append(parsed)
            elif parsed and listing_data.get('property_type') == 'other':
                parsed['property_type'] = property_type
                results.append(parsed)

        logger.info(f"Domain scrape: {len(results)} sold {property_type}s in {suburb}")

    except requests.RequestException as e:
        logger.error(f"Domain request failed for {suburb}: {e}")
    except Exception as e:
        logger.error(f"Domain scrape failed for {suburb}: {e}")

    return results
