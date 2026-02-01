# src/tracker/enrich/pipeline.py
"""Enrichment pipeline for sale classification."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tracker.db import Database
from tracker.enrich.classifier import has_exclude_keywords, should_auto_exclude
from tracker.enrich.domain import get_year_built
from tracker.enrich.zoning import get_zoning
from tracker.compute.segments import get_segment

logger = logging.getLogger(__name__)


def enrich_sale(
    address: str,
    suburb: str,
    postcode: str,
    description: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Enrich a sale with zoning, year built, and keyword detection.

    Args:
        address: Full street address
        suburb: Suburb name
        postcode: Postcode
        description: Property description (for keyword scanning)
        api_key: Domain API key (optional)

    Returns:
        Dict with enrichment data:
        - zoning: str or None
        - year_built: int or None
        - has_duplex_keywords: bool
    """
    # Get zoning from NSW Planning Portal
    full_address = f"{address}, {suburb} NSW {postcode}"
    zoning = get_zoning(full_address)

    # Get year built from Domain API (if key provided)
    year_built = None
    if api_key:
        year_built = get_year_built(address, suburb, postcode, api_key)

    # Scan for exclude keywords
    has_keywords = has_exclude_keywords(description)

    return {
        'zoning': zoning,
        'year_built': year_built,
        'has_duplex_keywords': has_keywords,
    }


def classify_sale(enrichment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify a sale based on enrichment data.

    Args:
        enrichment: Dict from enrich_sale()

    Returns:
        Dict with classification data:
        - is_auto_excluded: bool
        - auto_exclude_reason: str or None
        - review_status: 'pending' | 'auto_excluded'
        - use_in_median: bool (always False until manual approval)
    """
    is_excluded, reason = should_auto_exclude(
        zoning=enrichment.get('zoning'),
        year_built=enrichment.get('year_built'),
        has_keywords=enrichment.get('has_duplex_keywords', False),
    )

    return {
        'is_auto_excluded': is_excluded,
        'auto_exclude_reason': reason,
        'review_status': 'pending' if not is_excluded else 'not_comparable',
        'use_in_median': False,  # Never auto-approve
    }


def process_pending_sales(
    db: Database,
    segment_code: str,
    api_key: Optional[str] = None,
    limit: int = 50,
) -> int:
    """
    Process sales that haven't been classified yet.

    Finds sales in raw_sales that don't have a sale_classifications entry,
    enriches them, and creates classification records.

    Args:
        db: Database connection
        segment_code: Segment to process (e.g., 'revesby_houses')
        api_key: Domain API key (optional)
        limit: Max sales to process in one batch

    Returns:
        Number of sales processed
    """
    # Get segment configuration
    segment = get_segment(segment_code)
    if not segment:
        logger.warning(f"Unknown segment: {segment_code}")
        return 0

    # Build dynamic query based on segment config
    suburbs = list(segment.suburbs)
    placeholders = ','.join(['?' for _ in suburbs])

    query = f"""
        SELECT r.id, r.dealing_number, r.house_number, r.street_name,
               r.suburb, r.postcode, r.area_sqm
        FROM raw_sales r
        LEFT JOIN sale_classifications sc ON r.dealing_number = sc.sale_id
        WHERE sc.sale_id IS NULL
          AND LOWER(r.suburb) IN ({placeholders})
          AND r.property_type = ?
    """

    params: List = list(suburbs) + [segment.property_type]

    # Add area filter if specified
    if segment.area_min is not None:
        query += " AND r.area_sqm >= ?"
        params.append(segment.area_min)
    if segment.area_max is not None:
        query += " AND r.area_sqm <= ?"
        params.append(segment.area_max)

    # Add street filter if specified
    if segment.streets:
        street_list = list(segment.streets)
        street_placeholders = ','.join(['?' for _ in street_list])
        query += f" AND LOWER(r.street_name) IN ({street_placeholders})"
        params.extend(street_list)

    query += " ORDER BY r.contract_date DESC LIMIT ?"
    params.append(limit)

    sales = db.query(query, tuple(params))
    processed = 0

    for sale in sales:
        try:
            # Build address
            house_num = sale['house_number'] or ''
            street = sale['street_name']
            address = f"{house_num} {street}".strip()

            # Enrich
            enrichment = enrich_sale(
                address=address,
                suburb=sale['suburb'],
                postcode=sale['postcode'],
                description=None,  # NSW data doesn't have descriptions
                api_key=api_key,
            )

            # Classify
            classification = classify_sale(enrichment)

            # Save to database
            db.execute("""
                INSERT INTO sale_classifications (
                    sale_id, address, zoning, year_built, has_duplex_keywords,
                    is_auto_excluded, auto_exclude_reason, review_status, use_in_median
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sale['dealing_number'],
                f"{address}, {sale['suburb']}",
                enrichment['zoning'],
                enrichment['year_built'],
                enrichment['has_duplex_keywords'],
                classification['is_auto_excluded'],
                classification['auto_exclude_reason'],
                classification['review_status'],
                classification['use_in_median'],
            ))

            processed += 1
            logger.info(f"Classified sale {sale['dealing_number']}: excluded={classification['is_auto_excluded']}")

        except Exception as e:
            logger.error(f"Failed to process sale {sale.get('dealing_number')}: {e}")
            continue

    return processed
