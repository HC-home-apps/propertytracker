# src/tracker/ingest/domain_sold.py
"""Fetch sold listings from Domain API for provisional sales tracking."""

import json
import logging
import time
from typing import Dict, List, Optional

import requests

from tracker.ingest.normalise import normalise_address

logger = logging.getLogger(__name__)

DOMAIN_API_BASE = "https://api.domain.com.au/v1"
RATE_LIMIT_DELAY = 1.0

PROPERTY_TYPE_MAP = {
    'ApartmentUnitFlat': 'unit',
    'Unit': 'unit',
    'Studio': 'unit',
    'Apartment': 'unit',
    'House': 'house',
    'Townhouse': 'house',
    'Villa': 'house',
    'SemiDetached': 'house',
    'Duplex': 'house',
    'Terrace': 'house',
}


REVERSE_PROPERTY_TYPE_MAP = {
    'house': ['House', 'Townhouse', 'Villa', 'DuplexSemi-detached', 'Terrace'],
    'unit': ['ApartmentUnitFlat'],
}


def build_sold_search_body(suburb: str, property_type: str, postcode: str) -> dict:
    """Build POST body for Domain listings search API."""
    domain_types = REVERSE_PROPERTY_TYPE_MAP.get(property_type, ['House'])
    return {
        'listingType': 'Sold',
        'propertyTypes': domain_types,
        'locations': [
            {
                'suburb': suburb,
                'postcode': postcode,
                'state': 'NSW',
            }
        ],
        'pageSize': 20,
        'sort': {
            'sortKey': 'DateUpdated',
            'direction': 'Descending',
        },
    }


def parse_sold_listing(raw: dict) -> Optional[dict]:
    """Parse a single Domain API sold listing into provisional_sales format.

    Handles both the search endpoint response (nested under 'listing')
    and flat listing format.

    Returns None if the listing is missing required data (e.g., no price).
    """
    # Search endpoint wraps in {'type': ..., 'listing': {...}}
    listing = raw.get('listing', raw)

    # Extract property details (may be nested or flat)
    props = listing.get('propertyDetails', listing)

    domain_id = listing.get('id') or raw.get('id')
    if not domain_id:
        return None

    # Price: check saleDetails first, then flat fields
    sale_details = listing.get('saleDetails', {})
    price = sale_details.get('soldPrice') or listing.get('price') or raw.get('price')
    if not price:
        return None

    street_name = props.get('streetName', '') or props.get('street', '')
    street_type = props.get('streetType', '')
    full_street = f"{street_name} {street_type}".strip() if street_type else street_name

    domain_type = props.get('propertyType', '')
    property_type = PROPERTY_TYPE_MAP.get(domain_type, 'other')

    unit_number = props.get('unitNumber') or None
    house_number = props.get('streetNumber', '')
    suburb = props.get('suburb', '')
    postcode = props.get('postcode', '')

    sold_date = sale_details.get('soldDate', '') or listing.get('soldDate', '') or raw.get('soldDate', '')
    # Truncate datetime to date if needed (e.g. "2026-02-03T00:00:00" → "2026-02-03")
    if sold_date and 'T' in sold_date:
        sold_date = sold_date.split('T')[0]

    bedrooms = props.get('bedrooms')
    bathrooms = props.get('bathrooms')
    car_spaces = props.get('carSpaces') or props.get('carspaces')
    bedrooms = int(bedrooms) if bedrooms is not None else None
    bathrooms = int(bathrooms) if bathrooms is not None else None
    car_spaces = int(car_spaces) if car_spaces is not None else None

    # Build listing URL if we have enough info
    listing_url = None
    slug = listing.get('listingSlug', '')
    if slug:
        listing_url = f"https://www.domain.com.au/{slug}"

    address_normalised = normalise_address(
        unit_number=unit_number,
        house_number=house_number,
        street_name=full_street,
        suburb=suburb,
        postcode=postcode,
    )

    return {
        'id': f"domain-{domain_id}",
        'source': 'domain',
        'unit_number': unit_number,
        'house_number': house_number,
        'street_name': full_street,
        'suburb': suburb,
        'postcode': postcode,
        'property_type': property_type,
        'sold_price': int(price),
        'sold_date': sold_date,
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'car_spaces': car_spaces,
        'address_normalised': address_normalised,
        'listing_url': listing_url,
        'source_site': 'domain.com.au',
        'status': 'unconfirmed',
        'raw_json': json.dumps(raw),
    }


def fetch_sold_listings(
    suburb: str,
    property_type: str,
    postcode: str,
    api_key: Optional[str] = None,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[int] = None,
) -> List[dict]:
    """Fetch recent sold listings from Domain API search endpoint.

    Uses POST /v1/listings/residential/_search with listingType=Sold.

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        postcode: Postcode
        api_key: Domain API key (required)
        bedrooms: Optional bedroom count (not used in query)
        bathrooms: Optional bathroom count (not used in query)

    Returns:
        List of parsed provisional sale records.
    """
    if not api_key:
        logger.warning("No Domain API key provided, cannot fetch sold listings")
        return []

    results = []

    try:
        body = build_sold_search_body(suburb, property_type, postcode)

        headers = {
            'X-Api-Key': api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        time.sleep(RATE_LIMIT_DELAY)

        url = f"{DOMAIN_API_BASE}/listings/residential/_search"
        response = requests.post(url, headers=headers, json=body, timeout=30)

        if response.status_code == 200:
            listings = response.json()
            if not isinstance(listings, list):
                listings = [listings] if listings else []

            for raw in listings:
                parsed = parse_sold_listing(raw)
                if parsed and parsed['property_type'] == property_type:
                    results.append(parsed)

            logger.info(f"Domain API: {len(results)} sold {property_type}s in {suburb}")
        else:
            logger.warning(f"Domain API returned {response.status_code} for {suburb}")
            # Log response body for debugging
            try:
                logger.debug(f"Domain API response: {response.text[:200]}")
            except Exception:
                pass

    except requests.RequestException as e:
        logger.error(f"Domain API request failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error with Domain API: {e}")

    return results
