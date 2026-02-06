# src/tracker/ingest/domain_sold.py
"""Fetch sold listings from Domain API for provisional sales tracking."""

import json
import logging
import time
from typing import Dict, List, Optional

import requests

from tracker.ingest.normalise import normalise_address
from tracker.ingest.google_search import fetch_sold_listings_google

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

    # Parse bedrooms/bathrooms/car spaces (may not be present in all responses)
    bedrooms = raw.get('bedrooms')
    bathrooms = raw.get('bathrooms')
    car_spaces = raw.get('carSpaces') or raw.get('carspaces')
    bedrooms = int(bedrooms) if bedrooms is not None else None
    bathrooms = int(bathrooms) if bathrooms is not None else None
    car_spaces = int(car_spaces) if car_spaces is not None else None

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
        'listing_url': None,  # Domain API doesn't provide listing URLs
        'source_site': None,
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
    """Fetch recent sold listings using Google search (primary) and Domain API (bonus).

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        postcode: Postcode
        api_key: Optional Domain API key (for bonus data)
        bedrooms: Optional bedroom count for unit searches
        bathrooms: Optional bathroom count for unit searches

    Returns:
        List of parsed provisional sale records with listing_url, source_site, and status fields.
    """
    results = []

    # Primary source: Google search (always try, it's free)
    try:
        google_results = fetch_sold_listings_google(
            suburb=suburb,
            property_type=property_type,
            postcode=postcode,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
        )

        for listing in google_results:
            # Convert Google search format to provisional_sales format
            # Generate a unique ID from the normalised address
            addr_hash = hash(listing['address_normalised'])
            sale_id = f"google-{abs(addr_hash)}"

            # Set status based on price_withheld flag
            status = 'price_withheld' if listing.get('price_withheld', False) else 'unconfirmed'

            results.append({
                'id': sale_id,
                'source': 'google',
                'unit_number': listing.get('unit_number'),
                'house_number': listing.get('house_number', ''),
                'street_name': listing.get('street_name', ''),
                'suburb': listing.get('suburb', suburb),
                'postcode': listing.get('postcode', postcode),
                'property_type': property_type,
                'sold_price': listing.get('sold_price'),
                'sold_date': listing.get('sold_date'),
                'bedrooms': listing.get('bedrooms'),
                'bathrooms': listing.get('bathrooms'),
                'car_spaces': listing.get('car_spaces'),
                'address_normalised': listing['address_normalised'],
                'listing_url': listing.get('listing_url', ''),
                'source_site': listing.get('source_site', ''),
                'status': status,
                'raw_json': json.dumps(listing),
            })

        logger.info(f"Fetched {len(google_results)} sold {property_type}s in {suburb} from Google search")
    except Exception as e:
        logger.error(f"Google search failed: {e}")

    # Bonus source: Domain API (if api_key provided)
    if api_key:
        try:
            params = build_sold_search_params(suburb, property_type, postcode)

            headers = {
                'X-Api-Key': api_key,
                'Accept': 'application/json',
            }

            time.sleep(RATE_LIMIT_DELAY)

            url = f"{DOMAIN_API_BASE}/salesResults/{suburb}"
            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code == 200:
                listings = response.json()
                if not isinstance(listings, list):
                    listings = listings.get('listings', [])

                for raw in listings:
                    parsed = parse_sold_listing(raw)
                    if parsed and parsed['property_type'] == property_type:
                        results.append(parsed)

                logger.info(f"Fetched {len(listings)} sold {property_type}s in {suburb} from Domain API (bonus)")
            else:
                logger.warning(f"Domain API returned {response.status_code} for {suburb}")

        except requests.RequestException as e:
            logger.error(f"Domain API request failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error with Domain API: {e}")

    return results
