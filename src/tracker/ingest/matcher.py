# src/tracker/ingest/matcher.py
"""Match provisional Domain sales to VG records by normalised address."""

import logging
from datetime import datetime, timezone

from tracker.db import Database
from tracker.ingest.normalise import normalise_address

logger = logging.getLogger(__name__)

DATE_WINDOW_DAYS = 14


def match_provisional_to_vg(db: Database) -> int:
    """Match unconfirmed provisional sales to raw_sales by address + date.

    For each unconfirmed provisional sale, searches raw_sales for a record
    with matching normalised address within +-14 days of the sold date.

    When a match is found and the provisional sale has already been reviewed,
    the review decision is carried over to sale_classifications.

    Returns count of newly matched records.
    """
    unconfirmed = db.get_unconfirmed_provisional_sales()
    if not unconfirmed:
        return 0

    matched_count = 0

    for sale in unconfirmed:
        prov_normalised = sale['address_normalised']
        sold_date = sale['sold_date']
        suburb = sale['suburb']
        prop_type = sale['property_type']

        candidates = db.query(
            """SELECT dealing_number, unit_number, house_number,
                      street_name, suburb, postcode, contract_date, purchase_price
               FROM raw_sales
               WHERE LOWER(suburb) = LOWER(?)
                 AND property_type = ?
                 AND contract_date BETWEEN date(?, '-14 days') AND date(?, '+14 days')
            """,
            (suburb, prop_type, sold_date, sold_date)
        )

        for candidate in candidates:
            cand_normalised = normalise_address(
                unit_number=candidate['unit_number'],
                house_number=candidate['house_number'] or '',
                street_name=candidate['street_name'] or '',
                suburb=candidate['suburb'] or '',
                postcode=candidate['postcode'] or '',
            )

            if cand_normalised == prov_normalised:
                vg_price = candidate.get('purchase_price')
                prov_price = sale.get('sold_price')
                db.mark_provisional_confirmed(
                    sale['id'], candidate['dealing_number'],
                    vg_price=vg_price, provisional_price=prov_price,
                )
                if vg_price and prov_price and vg_price != prov_price:
                    logger.warning(
                        f"Price mismatch for {sale['id']}: "
                        f"provisional ${prov_price:,} -> VG ${vg_price:,} "
                        f"(diff ${abs(vg_price - prov_price):,})"
                    )
                logger.info(
                    f"Matched provisional {sale['id']} -> VG {candidate['dealing_number']}"
                )
                matched_count += 1

                # Carry over review decision if provisional was already reviewed
                review_status = sale.get('review_status')
                if review_status and review_status != 'pending':
                    _carryover_review(db, sale, candidate)

                break

    logger.info(f"Matched {matched_count}/{len(unconfirmed)} provisional sales to VG records")
    return matched_count


def _carryover_review(db: Database, prov_sale: dict, vg_candidate: dict):
    """Carry over a provisional review decision to sale_classifications.

    If a sale_classifications entry already exists (from enrichment), update it.
    Otherwise, create a new entry with the review decision.
    """
    dealing_number = vg_candidate['dealing_number']
    review_status = prov_sale['review_status']
    use_in_median = 1 if review_status == 'comparable' else 0
    reviewed_at = prov_sale.get('reviewed_at')
    listing_url = prov_sale.get('listing_url')
    now = datetime.now(timezone.utc).isoformat()

    # Check if sale_classifications entry already exists (from enrichment pipeline)
    existing = db.query(
        "SELECT sale_id, review_status FROM sale_classifications WHERE sale_id = ?",
        (dealing_number,)
    )

    if existing:
        # Only update if still pending (don't overwrite a manual review)
        if existing[0]['review_status'] == 'pending':
            db.execute("""
                UPDATE sale_classifications
                SET review_status = ?, use_in_median = ?, reviewed_at = ?, updated_at = ?
                WHERE sale_id = ?
            """, (review_status, use_in_median, reviewed_at or now, now, dealing_number))
            logger.info(f"Carried over review '{review_status}' to existing classification {dealing_number}")
    else:
        # Create new entry with review decision (enrichment will fill in details later)
        house_num = vg_candidate.get('house_number') or ''
        street = vg_candidate.get('street_name') or ''
        suburb = vg_candidate.get('suburb') or ''
        address = f"{house_num} {street}, {suburb}".strip()

        db.execute("""
            INSERT INTO sale_classifications (
                sale_id, address, review_status, use_in_median, reviewed_at, listing_url,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (dealing_number, address, review_status, use_in_median,
              reviewed_at or now, listing_url, now, now))
        logger.info(f"Created classification for {dealing_number} with carried-over review '{review_status}'")
