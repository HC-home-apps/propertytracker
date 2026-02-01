# src/tracker/enrich/domain.py
"""Domain API client for property year built lookup."""

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# Domain API base URL
DOMAIN_API_BASE = "https://api.domain.com.au/v1"

# Rate limiting
RATE_LIMIT_DELAY = 1.0  # seconds between requests


def build_domain_search_url(street_address: str, suburb: str, postcode: str) -> str:
    """
    Build Domain property search URL.

    Args:
        street_address: Street number and name
        suburb: Suburb name
        postcode: Postcode

    Returns:
        Full API URL for property lookup
    """
    # Construct full address for search
    full_address = f"{street_address}, {suburb} NSW {postcode}"
    encoded = quote(full_address)
    return f"{DOMAIN_API_BASE}/properties/_suggest?terms={encoded}"


def parse_year_built_from_response(response: Optional[Dict[str, Any]]) -> Optional[int]:
    """
    Extract year built from Domain API response.

    Args:
        response: JSON response from Domain API

    Returns:
        Year built as integer, or None if not found
    """
    if not response:
        return None

    try:
        property_details = response.get("propertyDetails", {})
        year = property_details.get("yearBuilt")
        return int(year) if year else None
    except (TypeError, ValueError):
        return None


def get_year_built(
    street_address: str,
    suburb: str,
    postcode: str,
    api_key: Optional[str] = None,
) -> Optional[int]:
    """
    Look up year built for a property via Domain API.

    Args:
        street_address: Street number and name (e.g., "15 Smith St")
        suburb: Suburb name
        postcode: Postcode
        api_key: Domain API key (required)

    Returns:
        Year built as integer, or None if not found/error
    """
    if not api_key:
        logger.debug("No Domain API key configured, skipping year built lookup")
        return None

    url = build_domain_search_url(street_address, suburb, postcode)

    headers = {
        "X-Api-Key": api_key,
        "Accept": "application/json",
    }

    try:
        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            logger.warning(f"Domain API returned {response.status_code} for {street_address}")
            return None

        data = response.json()
        return parse_year_built_from_response(data)

    except requests.RequestException as e:
        logger.error(f"Domain API request failed: {e}")
        return None
    except ValueError as e:
        logger.error(f"Failed to parse Domain API response: {e}")
        return None
