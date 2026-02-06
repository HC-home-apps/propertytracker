# src/tracker/ingest/matcher.py
"""Match provisional Domain sales to VG records by normalised address."""

import logging

from tracker.db import Database
from tracker.ingest.normalise import normalise_address

logger = logging.getLogger(__name__)

DATE_WINDOW_DAYS = 14


def match_provisional_to_vg(db: Database) -> int:
    """Match unconfirmed provisional sales to raw_sales by address + date.

    For each unconfirmed provisional sale, searches raw_sales for a record
    with matching normalised address within +-14 days of the sold date.

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
                      street_name, suburb, postcode, contract_date
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
                db.mark_provisional_confirmed(
                    sale['id'], candidate['dealing_number']
                )
                logger.info(
                    f"Matched provisional {sale['id']} -> VG {candidate['dealing_number']}"
                )
                matched_count += 1
                break

    logger.info(f"Matched {matched_count}/{len(unconfirmed)} provisional sales to VG records")
    return matched_count
