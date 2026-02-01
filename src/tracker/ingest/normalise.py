# src/tracker/ingest/normalise.py
"""Address normalisation for consistent matching of NSW property records."""

import re
from typing import Optional


# Street type abbreviations - map full names and variants to canonical short form
STREET_TYPE_MAP = {
    'street': 'st', 'st': 'st', 'str': 'st',
    'road': 'rd', 'rd': 'rd',
    'avenue': 'ave', 'ave': 'ave', 'av': 'ave',
    'drive': 'dr', 'dr': 'dr', 'dve': 'dr',
    'place': 'pl', 'pl': 'pl',
    'lane': 'ln', 'la': 'ln', 'ln': 'ln',
    'court': 'ct', 'ct': 'ct', 'crt': 'ct',
    'crescent': 'cres', 'cres': 'cres', 'cr': 'cres',
    'parade': 'pde', 'pde': 'pde',
    'highway': 'hwy', 'hwy': 'hwy',
    'terrace': 'tce', 'tce': 'tce', 'ter': 'tce',
    'close': 'cl', 'cl': 'cl',
    'way': 'way',
    'circuit': 'cct', 'cct': 'cct',
    'boulevard': 'bvd', 'blvd': 'bvd', 'bvd': 'bvd',
}

# Suburb variants - map known variations to canonical form
SUBURB_VARIANTS = {
    'lane cove': 'lane cove',
    'lane cove north': 'lane cove north',
    'lane cove west': 'lane cove west',
    'chatswood': 'chatswood',
    'chatswood west': 'chatswood west',
    'wollstonecraft': 'wollstonecraft',
    'revesby': 'revesby',
    'revesby heights': 'revesby heights',
}


def normalise_address(
    unit_number: Optional[str],
    house_number: str,
    street_name: str,
    suburb: str,
    postcode: str
) -> str:
    """
    Return canonical address string for matching.

    Format: "unit|house|street|suburb|postcode" (all lowercase, normalised)

    Args:
        unit_number: Unit/apartment number or None
        house_number: Street number (may contain unit in "2/10" format)
        street_name: Full street name with type
        suburb: Suburb name
        postcode: 4-digit postcode

    Returns:
        Pipe-delimited normalised address string
    """
    unit = normalise_unit(unit_number, house_number)
    house = normalise_house_number(house_number)
    street = normalise_street(street_name)
    suburb_norm = normalise_suburb(suburb)
    postcode_norm = postcode.strip() if postcode else ''

    return f"{unit}|{house}|{street}|{suburb_norm}|{postcode_norm}"


def normalise_unit(unit_field: Optional[str], house_field: str) -> str:
    """
    Extract and normalise unit number from either field.

    Handles:
    - "2/10" -> unit="2"
    - "Unit 2, 10 Smith St" -> unit="2"
    - "Apartment 5A" -> unit="5a"
    - None -> ""

    Args:
        unit_field: Explicit unit number field (may be None)
        house_field: House number which may contain "unit/house" format

    Returns:
        Normalised unit number or empty string
    """
    if unit_field:
        unit = unit_field.lower().strip()
        # Remove common prefixes
        unit = re.sub(r'^(unit|apt|apartment|suite|flat|shop)\s*', '', unit)
        # Remove trailing punctuation/whitespace
        unit = re.sub(r'[,\s]+$', '', unit)
        return unit

    # Check if house_field contains unit (e.g., "2/10" or "3/10-12")
    match = re.match(r'^(\d+[a-z]?)\s*/\s*(.+)$', house_field, re.IGNORECASE)
    if match:
        return match.group(1).lower()

    return ''


def normalise_house_number(house_field: str) -> str:
    """
    Extract and normalise street number.

    Handles:
    - "10" -> "10"
    - "10-12" -> "10-12"
    - "2/10" -> "10" (unit extracted separately)
    - "10A" -> "10a"

    Args:
        house_field: Raw house number from data

    Returns:
        Normalised house number
    """
    house = house_field.lower().strip()

    # Remove leading unit pattern (e.g., "2/10" -> "10")
    match = re.match(r'^\d+[a-z]?\s*/\s*(.+)$', house, re.IGNORECASE)
    if match:
        house = match.group(1)

    # Normalise range separators (en-dash, em-dash -> hyphen)
    house = re.sub(r'\s*[–—]\s*', '-', house)
    # Remove extra spaces around hyphen
    house = re.sub(r'\s*-\s*', '-', house)

    return house


def normalise_street(street_name: str) -> str:
    """
    Normalise street name with canonical abbreviations.

    - Lowercase
    - Remove punctuation (except hyphens)
    - Canonical street type abbreviations
    - Normalised whitespace

    Args:
        street_name: Raw street name

    Returns:
        Normalised street name
    """
    street = street_name.lower().strip()

    # Remove punctuation except hyphens
    street = re.sub(r"[.,\'\"]+", '', street)

    # Normalise whitespace
    street = re.sub(r'\s+', ' ', street)

    # Split into words
    words = street.split()

    # Normalise street type (usually last word)
    if words:
        last = words[-1]
        if last in STREET_TYPE_MAP:
            words[-1] = STREET_TYPE_MAP[last]

    return ' '.join(words)


def normalise_suburb(suburb: str) -> str:
    """
    Normalise suburb name to canonical form.

    - Lowercase
    - Strip whitespace
    - Map known variants

    Args:
        suburb: Raw suburb name

    Returns:
        Canonical suburb name
    """
    suburb = suburb.lower().strip()
    return SUBURB_VARIANTS.get(suburb, suburb)
