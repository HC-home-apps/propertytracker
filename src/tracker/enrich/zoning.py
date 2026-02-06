# src/tracker/enrich/zoning.py
"""NSW Planning Portal client for zoning lookup."""

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# NSW Planning Portal API (public, no auth required)
NSW_PLANNING_API = "https://api.apps1.nsw.gov.au/planning/viewersf/V1/ePlanningApi"

# Rate limiting
RATE_LIMIT_DELAY = 1.0  # seconds between requests


def _search_address(street_address: str, suburb: str, postcode: str) -> Optional[int]:
    """
    Search for a property ID using structured address components.

    Returns propId if found, None otherwise.
    """
    url = (
        f"{NSW_PLANNING_API}/address"
        f"?a={quote(street_address)}&s={quote(suburb)}&p={quote(postcode)}"
    )

    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logger.warning(f"NSW Planning address search returned {response.status_code}")
            return None

        data = response.json()
        if not data or not isinstance(data, list):
            return None

        # Return propId of first match
        return data[0].get("propId")

    except (requests.RequestException, ValueError) as e:
        logger.error(f"NSW Planning address search failed: {e}")
        return None


def _get_zoning_from_layers(prop_id: int) -> Optional[str]:
    """
    Look up zoning code from layer intersect API using property ID.

    Returns zone code (R2, R3, B1, etc.) or None.
    """
    url = f"{NSW_PLANNING_API}/layerintersect?type=property&id={prop_id}"

    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logger.warning(f"NSW Planning layer intersect returned {response.status_code}")
            return None

        data = response.json()
        if not data or not isinstance(data, list):
            return None

        # Find the "Land Zoning Map" layer
        for layer in data:
            if layer.get("layerName") == "Land Zoning Map":
                results = layer.get("results", [])
                if results:
                    return results[0].get("Zone")

        return None

    except (requests.RequestException, ValueError) as e:
        logger.error(f"NSW Planning layer intersect failed: {e}")
        return None


def get_zoning(full_address: str, street_address: str = "",
               suburb: str = "", postcode: str = "") -> Optional[str]:
    """
    Look up zoning for an address via NSW Planning Portal.

    Uses two-step API: structured address search to get propId,
    then layer intersect to get zoning code.

    Args:
        full_address: Full address string (unused, kept for backwards compat)
        street_address: Street number and name (e.g., "15 Smith St")
        suburb: Suburb name (e.g., "Revesby")
        postcode: Postcode (e.g., "2212")

    Returns:
        Zoning code (R2, R3, etc.) or None if not found/error
    """
    if not street_address or not suburb:
        logger.debug(f"Missing structured address components, skipping zoning for: {full_address}")
        return None

    # Rate limiting
    time.sleep(RATE_LIMIT_DELAY)

    # Step 1: Search for property ID
    prop_id = _search_address(street_address, suburb, postcode)
    if not prop_id:
        logger.debug(f"No property found for {street_address}, {suburb} {postcode}")
        return None

    # Step 2: Get zoning from layer intersect
    time.sleep(RATE_LIMIT_DELAY)
    zone = _get_zoning_from_layers(prop_id)

    if zone:
        logger.info(f"Zoning for {street_address}, {suburb}: {zone}")
    else:
        logger.debug(f"No zoning found for propId {prop_id}")

    return zone
