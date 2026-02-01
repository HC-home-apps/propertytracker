# src/tracker/ingest/parser.py
"""Parse NSW Property Sales CSV files from nswpropertysalesdata.com."""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, List, Optional, Set

logger = logging.getLogger(__name__)

# District codes for target suburbs
# Canterbury-Bankstown (Revesby): 108
# North Sydney (Wollstonecraft): 118
# Lane Cove: 87
# Willoughby (Chatswood): 145
TARGET_DISTRICTS = {108, 118, 87, 145}

# Target suburbs (lowercase for matching)
TARGET_SUBURBS = {
    'revesby', 'revesby heights',
    'wollstonecraft',
    'lane cove', 'lane cove north', 'lane cove west',
    'chatswood', 'chatswood west',
}


def parse_csv_file(
    file_path: Path,
    districts: Optional[Set[int]] = None,
    suburbs: Optional[Set[str]] = None,
) -> Generator[Dict, None, None]:
    """
    Parse a single CSV file and yield sales records.

    Args:
        file_path: Path to CSV file
        districts: Set of district codes to include (None = all)
        suburbs: Set of suburb names to include (None = all)

    Yields:
        Dict with normalised sale record fields
    """
    if districts is None:
        districts = TARGET_DISTRICTS
    if suburbs is None:
        suburbs = TARGET_SUBURBS

    logger.info(f"Parsing {file_path}")

    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)

        for row in reader:
            # Filter by district if specified
            district_code = _safe_int(row.get('district_code', row.get('District Code', '')))
            if districts and district_code not in districts:
                continue

            # Filter by suburb if specified
            suburb = row.get('suburb', row.get('Suburb', '')).strip().lower()
            if suburbs and suburb not in suburbs:
                continue

            # Parse and normalise the record
            record = _parse_row(row, file_path.name)
            if record:
                yield record


def _parse_row(row: Dict, source_file: str) -> Optional[Dict]:
    """
    Parse a single CSV row into a normalised record.

    Args:
        row: Raw CSV row dict
        source_file: Name of source file for tracking

    Returns:
        Normalised record dict or None if invalid
    """
    try:
        # Handle different column name formats
        dealing_number = row.get('dealing_number', row.get('Dealing Number', '')).strip()
        if not dealing_number:
            return None

        # Price validation
        price_str = row.get('purchase_price', row.get('Purchase Price', '0'))
        price = _safe_int(price_str)
        if price <= 0 or price > 100_000_000:  # Sanity check
            return None

        # Date parsing
        contract_date = _parse_date(
            row.get('contract_date', row.get('Contract Date', ''))
        )
        if not contract_date:
            return None

        settlement_date = _parse_date(
            row.get('settlement_date', row.get('Settlement Date', ''))
        )

        # Extract address components
        unit_number = row.get('unit_number', row.get('Unit Number', '')).strip() or None
        house_number = row.get('house_number', row.get('House Number', '')).strip()
        street_name = row.get('street_name', row.get('Street Name', '')).strip()
        suburb = row.get('suburb', row.get('Suburb', '')).strip()
        postcode = row.get('postcode', row.get('Postcode', '')).strip()

        # Property classification
        strata_lot = row.get('strata_lot_number', row.get('Strata Lot Number', '')).strip()
        nature = row.get('nature_of_property', row.get('Nature Of Property', '')).strip()

        # Determine property type
        property_type = classify_property_type(strata_lot, nature)

        return {
            'dealing_number': dealing_number,
            'property_id': row.get('property_id', row.get('Property ID', '')).strip(),
            'unit_number': unit_number,
            'house_number': house_number,
            'street_name': street_name,
            'suburb': suburb,
            'postcode': postcode,
            'area_sqm': _safe_float(row.get('area', row.get('Area', ''))),
            'zone_code': row.get('zone_code', row.get('Zone Code', '')).strip(),
            'nature_of_property': nature,
            'strata_lot_number': strata_lot if strata_lot else None,
            'contract_date': contract_date,
            'settlement_date': settlement_date,
            'purchase_price': price,
            'property_type': property_type,
            'district_code': _safe_int(row.get('district_code', row.get('District Code', ''))),
            'source_file': source_file,
        }

    except Exception as e:
        logger.warning(f"Error parsing row: {e}")
        return None


def classify_property_type(strata_lot: str, nature: str) -> str:
    """
    Classify property as house, unit, land, or other.

    Args:
        strata_lot: Strata lot number (indicates unit/apartment)
        nature: Nature of property field

    Returns:
        Property type: 'house', 'unit', 'land', or 'other'
    """
    nature_lower = nature.lower() if nature else ''

    # Strata lot indicates unit/apartment
    if strata_lot:
        return 'unit'

    # Check nature for indicators
    if 'vacant' in nature_lower or 'land' in nature_lower:
        return 'land'

    if 'residence' in nature_lower:
        # Could be house or unit - default to house if no strata
        return 'house'

    if 'unit' in nature_lower or 'flat' in nature_lower or 'apartment' in nature_lower:
        return 'unit'

    if 'house' in nature_lower or 'dwelling' in nature_lower:
        return 'house'

    # Default to house for residential
    if 'commercial' in nature_lower or 'industrial' in nature_lower:
        return 'other'

    return 'house'


def _parse_date(date_str: str) -> Optional[str]:
    """Parse date string to ISO format YYYY-MM-DD."""
    if not date_str:
        return None

    date_str = date_str.strip()

    # Try common formats
    formats = [
        '%Y-%m-%d',      # ISO
        '%d/%m/%Y',      # AU format
        '%d-%m-%Y',      # AU with dashes
        '%Y/%m/%d',      # Alternate ISO
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    logger.warning(f"Could not parse date: {date_str}")
    return None


def _safe_int(value: str) -> int:
    """Safely parse integer, returning 0 on failure."""
    if not value:
        return 0
    try:
        # Handle comma-formatted numbers
        clean = str(value).replace(',', '').replace('$', '').strip()
        return int(float(clean))
    except (ValueError, TypeError):
        return 0


def _safe_float(value: str) -> Optional[float]:
    """Safely parse float, returning None on failure."""
    if not value:
        return None
    try:
        clean = str(value).replace(',', '').strip()
        return float(clean)
    except (ValueError, TypeError):
        return None


def parse_all_csv_files(
    directory: Path,
    districts: Optional[Set[int]] = None,
    suburbs: Optional[Set[str]] = None,
) -> Generator[Dict, None, None]:
    """
    Parse all CSV files in a directory.

    Args:
        directory: Directory containing CSV files
        districts: Set of district codes to include
        suburbs: Set of suburb names to include

    Yields:
        Normalised sale records
    """
    directory = Path(directory)
    csv_files = list(directory.glob('*.csv'))

    logger.info(f"Found {len(csv_files)} CSV files in {directory}")

    for csv_file in sorted(csv_files):
        yield from parse_csv_file(csv_file, districts, suburbs)
