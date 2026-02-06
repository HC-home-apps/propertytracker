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


def build_sold_search_params(suburb: str, property_type: str, postcode: str) -> dict:
    """Build search parameters for Domain sold listings API."""
    return {
        'suburb': suburb,
        'postcode': postcode,
        'propertyTypes': [property_type],
    }


def parse_sold_listing(raw: dict) -> Optional[dict]:
    """Parse a single Domain API sold listing into provisional_sales format.

    Returns None if the listing is missing required data (e.g., no price).
    """
    price = raw.get('price')
    if not price:
        return None

    domain_id = raw.get('id')
    if not domain_id:
        return None

    street_name = raw.get('streetName', '')
    street_type = raw.get('streetType', '')
    full_street = f"{street_name} {street_type}".strip()

    domain_type = raw.get('propertyType', '')
    property_type = PROPERTY_TYPE_MAP.get(domain_type, 'other')

    unit_number = raw.get('unitNumber') or None
    house_number = raw.get('streetNumber', '')
    suburb = raw.get('suburb', '')
    postcode = raw.get('postcode', '')
    sold_date = raw.get('soldDate', '')

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
        'address_normalised': address_normalised,
        'raw_json': json.dumps(raw),
    }


def fetch_sold_listings(
    suburb: str, property_type: str, postcode: str, api_key: Optional[str] = None,
) -> List[dict]:
    """Fetch recent sold listings from Domain API for a suburb.

    Returns list of parsed provisional sale records, or empty list on error.
    """
    if not api_key:
        logger.debug("No Domain API key, skipping sold listings fetch")
        return []

    params = build_sold_search_params(suburb, property_type, postcode)

    headers = {
        'X-Api-Key': api_key,
        'Accept': 'application/json',
    }

    try:
        time.sleep(RATE_LIMIT_DELAY)

        url = f"{DOMAIN_API_BASE}/salesResults/{suburb}"
        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code != 200:
            logger.warning(f"Domain sold listings API returned {response.status_code} for {suburb}")
            return []

        listings = response.json()
        if not isinstance(listings, list):
            listings = listings.get('listings', [])

        results = []
        for raw in listings:
            parsed = parse_sold_listing(raw)
            if parsed and parsed['property_type'] == property_type:
                results.append(parsed)

        logger.info(f"Fetched {len(results)} sold {property_type}s in {suburb} from Domain")
        return results

    except requests.RequestException as e:
        logger.error(f"Domain sold listings request failed: {e}")
        return []
    except (ValueError, KeyError) as e:
        logger.error(f"Failed to parse Domain sold listings response: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching Domain sold listings: {e}")
        return []
