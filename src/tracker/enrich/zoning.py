# src/tracker/enrich/zoning.py
"""NSW Planning Portal client for zoning lookup."""

import logging
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# NSW Planning Portal API (public, no auth required)
NSW_PLANNING_API = "https://api.apps1.nsw.gov.au/planning/viewersf/V1/ePlanningApi"

# Rate limiting
RATE_LIMIT_DELAY = 1.0  # seconds between requests

# Regex to extract zone code (R2, R3, B1, etc.) from zone name
ZONE_CODE_PATTERN = re.compile(r'^([A-Z]\d+)')


def build_zoning_url(address: str) -> str:
    """
    Build NSW Planning Portal zoning lookup URL.

    Args:
        address: Full address string

    Returns:
        API URL for zoning lookup
    """
    encoded = quote(address)
    return f"{NSW_PLANNING_API}/address?address={encoded}"


def parse_zoning_from_response(response: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Extract zoning code from NSW Planning Portal response.

    Args:
        response: JSON response from Planning Portal

    Returns:
        Zoning code (R2, R3, B1, etc.) or None if not found
    """
    if not response:
        return None

    try:
        zone_name = response.get("zoning", {}).get("zoneName", "")
        if not zone_name:
            return None

        # Extract zone code from name like "R2 Low Density Residential"
        match = ZONE_CODE_PATTERN.match(zone_name)
        return match.group(1) if match else None

    except (TypeError, AttributeError):
        return None


def get_zoning(address: str) -> Optional[str]:
    """
    Look up zoning for an address via NSW Planning Portal.

    Args:
        address: Full address string (e.g., "15 Smith St, Revesby NSW 2212")

    Returns:
        Zoning code (R2, R3, etc.) or None if not found/error
    """
    url = build_zoning_url(address)

    try:
        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            logger.warning(f"NSW Planning API returned {response.status_code} for {address}")
            return None

        data = response.json()
        return parse_zoning_from_response(data)

    except requests.RequestException as e:
        logger.error(f"NSW Planning API request failed: {e}")
        return None
    except ValueError as e:
        logger.error(f"Failed to parse NSW Planning API response: {e}")
        return None
