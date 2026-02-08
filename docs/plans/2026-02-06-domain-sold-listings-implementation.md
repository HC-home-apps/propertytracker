# Domain Sold Listings Integration - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Domain API sold listings as a provisional data source to close the ~2 month VG data lag.

**Architecture:** New `provisional_sales` table stores Domain sold data separately from `raw_sales`. Address normalisation matches Domain records to VG records when they arrive. Domain sales appear in reports as "unconfirmed" but never affect median calculations.

**Tech Stack:** Python 3.9+, SQLite, requests, existing normalise.py

**Design doc:** `docs/plans/2026-02-06-domain-sold-listings-integration.md`

---

### Task 1: Database Schema - provisional_sales table

**Files:**
- Modify: `src/tracker/db.py` (add table creation in `init_schema()`, add insert/query methods)
- Test: `tests/test_db.py`

**Step 1: Write the failing tests**

Add to `tests/test_db.py`:

```python
class TestProvisionalSalesTable:
    """Test provisional_sales table operations."""

    def test_table_exists(self, db):
        """provisional_sales table should be created by init_schema."""
        tables = db.list_tables()
        assert 'provisional_sales' in tables

    def test_insert_provisional_sale(self, db):
        """Should insert a provisional sale record."""
        inserted = db.upsert_provisional_sales([{
            'id': 'domain-12345',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27-29',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'address_normalised': '9|27-29|morton st|wollstonecraft|2065',
            'raw_json': '{"test": true}',
        }])
        assert inserted == 1

    def test_provisional_dedup_on_id(self, db):
        """Should ignore duplicate provisional sales by id."""
        sale = {
            'id': 'domain-12345',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27-29',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'address_normalised': '9|27-29|morton st|wollstonecraft|2065',
            'raw_json': '{}',
        }
        db.upsert_provisional_sales([sale])
        inserted = db.upsert_provisional_sales([sale])
        assert inserted == 0

    def test_get_unconfirmed_provisional(self, db):
        """Should return only unconfirmed provisional sales."""
        db.upsert_provisional_sales([{
            'id': 'domain-111',
            'source': 'domain',
            'unit_number': None,
            'house_number': '10',
            'street_name': 'Smith St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 900000,
            'sold_date': '2026-01-15',
            'address_normalised': '|10|smith st|wollstonecraft|2065',
            'raw_json': '{}',
        }])
        results = db.get_unconfirmed_provisional_sales()
        assert len(results) == 1
        assert results[0]['id'] == 'domain-111'
        assert results[0]['status'] == 'unconfirmed'

    def test_mark_provisional_confirmed(self, db):
        """Should link provisional sale to VG dealing number."""
        db.upsert_provisional_sales([{
            'id': 'domain-222',
            'source': 'domain',
            'unit_number': None,
            'house_number': '10',
            'street_name': 'Smith St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 900000,
            'sold_date': '2026-01-15',
            'address_normalised': '|10|smith st|wollstonecraft|2065',
            'raw_json': '{}',
        }])
        db.mark_provisional_confirmed('domain-222', 'AU123456')
        results = db.get_unconfirmed_provisional_sales()
        assert len(results) == 0  # No longer unconfirmed

    def test_get_provisional_for_report(self, db):
        """Should return unconfirmed sales grouped by suburb for report display."""
        db.upsert_provisional_sales([
            {
                'id': 'domain-aaa',
                'source': 'domain',
                'unit_number': '9',
                'house_number': '27',
                'street_name': 'Morton St',
                'suburb': 'Wollstonecraft',
                'postcode': '2065',
                'property_type': 'unit',
                'sold_price': 1200000,
                'sold_date': '2026-02-03',
                'address_normalised': '9|27|morton st|wollstonecraft|2065',
                'raw_json': '{}',
            },
            {
                'id': 'domain-bbb',
                'source': 'domain',
                'unit_number': None,
                'house_number': '5',
                'street_name': 'Smith St',
                'suburb': 'Revesby',
                'postcode': '2212',
                'property_type': 'house',
                'sold_price': 1500000,
                'sold_date': '2026-02-01',
                'address_normalised': '|5|smith st|revesby|2212',
                'raw_json': '{}',
            },
        ])
        results = db.get_unconfirmed_provisional_sales(suburb='Wollstonecraft')
        assert len(results) == 1
        assert results[0]['suburb'] == 'Wollstonecraft'
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py::TestProvisionalSalesTable -v`
Expected: FAIL (table doesn't exist, methods don't exist)

**Step 3: Implement schema and DB methods**

In `src/tracker/db.py`, add to `init_schema()` after the `sale_classifications` table (before `conn.commit()`):

```python
        # 8. provisional_sales - Sold listings from Domain API (unconfirmed)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provisional_sales (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                unit_number TEXT,
                house_number TEXT,
                street_name TEXT,
                suburb TEXT NOT NULL,
                postcode TEXT,
                property_type TEXT CHECK(property_type IN ('house', 'unit', 'land', 'other')),
                sold_price INTEGER,
                sold_date DATE,
                address_normalised TEXT,
                matched_dealing_number TEXT,
                status TEXT DEFAULT 'unconfirmed'
                    CHECK(status IN ('unconfirmed', 'confirmed', 'superseded')),
                raw_json TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_provisional_sales_status
            ON provisional_sales(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_provisional_sales_suburb
            ON provisional_sales(suburb, sold_date)
        """)
```

Add these methods to the `Database` class:

```python
    def upsert_provisional_sales(self, sales: list) -> int:
        """Insert provisional sales, ignoring duplicates. Returns count of new records."""
        if not sales:
            return 0

        sql = """
            INSERT OR IGNORE INTO provisional_sales (
                id, source, unit_number, house_number, street_name,
                suburb, postcode, property_type, sold_price, sold_date,
                address_normalised, raw_json
            ) VALUES (
                :id, :source, :unit_number, :house_number, :street_name,
                :suburb, :postcode, :property_type, :sold_price, :sold_date,
                :address_normalised, :raw_json
            )
        """

        conn = self._get_conn()
        cursor = conn.cursor()
        inserted = 0

        for sale in sales:
            cursor.execute(sql, sale)
            if cursor.rowcount > 0:
                inserted += 1

        conn.commit()
        return inserted

    def get_unconfirmed_provisional_sales(self, suburb: str = None) -> list:
        """Get unconfirmed provisional sales, optionally filtered by suburb."""
        if suburb:
            return self.query(
                """SELECT * FROM provisional_sales
                   WHERE status = 'unconfirmed' AND LOWER(suburb) = LOWER(?)
                   ORDER BY sold_date DESC""",
                (suburb,)
            )
        return self.query(
            """SELECT * FROM provisional_sales
               WHERE status = 'unconfirmed'
               ORDER BY sold_date DESC"""
        )

    def mark_provisional_confirmed(self, provisional_id: str, dealing_number: str):
        """Link a provisional sale to a VG record."""
        self.execute(
            """UPDATE provisional_sales
               SET status = 'confirmed', matched_dealing_number = ?
               WHERE id = ?""",
            (dealing_number, provisional_id)
        )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_db.py::TestProvisionalSalesTable -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All 250+ tests PASS (no regressions)

**Step 6: Commit**

```bash
git add src/tracker/db.py tests/test_db.py
git commit -m "feat: add provisional_sales table for Domain sold listings"
```

---

### Task 2: Domain API sold listings client

**Files:**
- Create: `src/tracker/ingest/domain_sold.py`
- Test: `tests/test_domain_sold.py`

**Reference:** Existing Domain API pattern in `src/tracker/enrich/domain.py` (auth, rate limiting, error handling)

**Step 1: Write the failing tests**

Create `tests/test_domain_sold.py`:

```python
# tests/test_domain_sold.py
import pytest
from unittest.mock import patch, MagicMock
from tracker.ingest.domain_sold import (
    fetch_sold_listings,
    parse_sold_listing,
    build_sold_search_params,
)


class TestBuildSoldSearchParams:
    """Test search parameter construction."""

    def test_builds_params_for_suburb(self):
        params = build_sold_search_params(
            suburb='Wollstonecraft',
            property_type='unit',
            postcode='2065',
        )
        assert params['suburb'] == 'Wollstonecraft'
        assert params['propertyTypes'] == ['unit']
        assert params['postcode'] == '2065'

    def test_maps_house_type(self):
        params = build_sold_search_params(
            suburb='Revesby',
            property_type='house',
            postcode='2212',
        )
        assert params['propertyTypes'] == ['house']


class TestParseSoldListing:
    """Test parsing Domain API sold listing response into our format."""

    def test_parses_unit_listing(self):
        raw = {
            'id': 12345,
            'propertyDetailsUrl': '/property/...',
            'unitNumber': '9',
            'streetNumber': '27-29',
            'streetName': 'Morton',
            'streetType': 'Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'propertyType': 'ApartmentUnitFlat',
            'price': 1200000,
            'soldDate': '2026-02-03',
        }
        result = parse_sold_listing(raw)
        assert result['id'] == 'domain-12345'
        assert result['source'] == 'domain'
        assert result['unit_number'] == '9'
        assert result['house_number'] == '27-29'
        assert result['street_name'] == 'Morton Street'
        assert result['suburb'] == 'Wollstonecraft'
        assert result['property_type'] == 'unit'
        assert result['sold_price'] == 1200000
        assert result['sold_date'] == '2026-02-03'

    def test_parses_house_listing(self):
        raw = {
            'id': 67890,
            'unitNumber': None,
            'streetNumber': '5',
            'streetName': 'Smith',
            'streetType': 'Street',
            'suburb': 'Revesby',
            'postcode': '2212',
            'propertyType': 'House',
            'price': 1500000,
            'soldDate': '2026-01-20',
        }
        result = parse_sold_listing(raw)
        assert result['id'] == 'domain-67890'
        assert result['property_type'] == 'house'
        assert result['unit_number'] is None

    def test_skips_listing_without_price(self):
        raw = {
            'id': 99999,
            'unitNumber': None,
            'streetNumber': '1',
            'streetName': 'Fake',
            'streetType': 'Street',
            'suburb': 'Revesby',
            'postcode': '2212',
            'propertyType': 'House',
            'price': None,
            'soldDate': '2026-01-20',
        }
        result = parse_sold_listing(raw)
        assert result is None


class TestFetchSoldListings:
    """Test Domain API sold listings fetching."""

    @patch('tracker.ingest.domain_sold.requests.get')
    def test_returns_parsed_listings(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                'id': 11111,
                'unitNumber': '3',
                'streetNumber': '10',
                'streetName': 'Test',
                'streetType': 'Road',
                'suburb': 'Wollstonecraft',
                'postcode': '2065',
                'propertyType': 'ApartmentUnitFlat',
                'price': 800000,
                'soldDate': '2026-01-15',
            }
        ]
        mock_get.return_value = mock_response

        results = fetch_sold_listings(
            suburb='Wollstonecraft',
            property_type='unit',
            postcode='2065',
            api_key='test-key',
        )
        assert len(results) == 1
        assert results[0]['id'] == 'domain-11111'

    @patch('tracker.ingest.domain_sold.requests.get')
    def test_returns_empty_on_api_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        results = fetch_sold_listings(
            suburb='Wollstonecraft',
            property_type='unit',
            postcode='2065',
            api_key='test-key',
        )
        assert results == []

    def test_returns_empty_without_api_key(self):
        results = fetch_sold_listings(
            suburb='Wollstonecraft',
            property_type='unit',
            postcode='2065',
            api_key=None,
        )
        assert results == []
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_domain_sold.py -v`
Expected: FAIL (module doesn't exist)

**Step 3: Implement the Domain API client**

Create `src/tracker/ingest/domain_sold.py`:

```python
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

# Map Domain property types to our types
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


def build_sold_search_params(
    suburb: str,
    property_type: str,
    postcode: str,
) -> dict:
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

    # Map Domain property type to ours
    domain_type = raw.get('propertyType', '')
    property_type = PROPERTY_TYPE_MAP.get(domain_type, 'other')

    unit_number = raw.get('unitNumber') or None
    house_number = raw.get('streetNumber', '')
    suburb = raw.get('suburb', '')
    postcode = raw.get('postcode', '')
    sold_date = raw.get('soldDate', '')

    # Compute normalised address for matching
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
    suburb: str,
    property_type: str,
    postcode: str,
    api_key: Optional[str] = None,
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
            logger.warning(
                f"Domain sold listings API returned {response.status_code} for {suburb}"
            )
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_sold.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/tracker/ingest/domain_sold.py tests/test_domain_sold.py
git commit -m "feat: add Domain API sold listings client"
```

---

### Task 3: Address matcher - link provisional to VG records

**Files:**
- Create: `src/tracker/ingest/matcher.py`
- Test: `tests/test_matcher.py`

**Step 1: Write the failing tests**

Create `tests/test_matcher.py`:

```python
# tests/test_matcher.py
import pytest
from tracker.db import Database
from tracker.ingest.matcher import match_provisional_to_vg


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / 'test.db'))
    db.init_schema()
    return db


class TestMatchProvisionalToVG:
    """Test address-based matching of Domain sales to VG records."""

    def test_matches_exact_address(self, db):
        """Should match when normalised addresses are identical."""
        # Insert a VG sale
        db.upsert_raw_sales([{
            'dealing_number': 'AU999999',
            'property_id': '791136.0',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'area_sqm': None,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': '9.0',
            'contract_date': '2026-02-03',
            'settlement_date': '2026-03-15',
            'purchase_price': 1200000,
            'property_type': 'unit',
            'district_code': 118,
            'source_file': 'test.csv',
        }])

        # Insert a matching provisional sale
        db.upsert_provisional_sales([{
            'id': 'domain-12345',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'address_normalised': '9|27|morton st|wollstonecraft|2065',
            'raw_json': '{}',
        }])

        matched = match_provisional_to_vg(db)
        assert matched == 1

        # Verify it's no longer unconfirmed
        unconfirmed = db.get_unconfirmed_provisional_sales()
        assert len(unconfirmed) == 0

    def test_no_match_different_address(self, db):
        """Should not match when addresses differ."""
        db.upsert_raw_sales([{
            'dealing_number': 'AU888888',
            'property_id': '',
            'unit_number': '5',
            'house_number': '10',
            'street_name': 'Smith St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'area_sqm': None,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': '5.0',
            'contract_date': '2026-02-03',
            'settlement_date': '2026-03-15',
            'purchase_price': 900000,
            'property_type': 'unit',
            'district_code': 118,
            'source_file': 'test.csv',
        }])

        db.upsert_provisional_sales([{
            'id': 'domain-99999',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'address_normalised': '9|27|morton st|wollstonecraft|2065',
            'raw_json': '{}',
        }])

        matched = match_provisional_to_vg(db)
        assert matched == 0

    def test_no_match_outside_date_window(self, db):
        """Should not match when dates are more than 14 days apart."""
        db.upsert_raw_sales([{
            'dealing_number': 'AU777777',
            'property_id': '',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'area_sqm': None,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': '9.0',
            'contract_date': '2026-03-01',
            'settlement_date': '2026-04-15',
            'purchase_price': 1200000,
            'property_type': 'unit',
            'district_code': 118,
            'source_file': 'test.csv',
        }])

        db.upsert_provisional_sales([{
            'id': 'domain-77777',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-01',
            'address_normalised': '9|27|morton st|wollstonecraft|2065',
            'raw_json': '{}',
        }])

        matched = match_provisional_to_vg(db)
        assert matched == 0

    def test_empty_provisional_returns_zero(self, db):
        """Should return 0 when no provisional sales exist."""
        matched = match_provisional_to_vg(db)
        assert matched == 0
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_matcher.py -v`
Expected: FAIL (module doesn't exist)

**Step 3: Implement the matcher**

Create `src/tracker/ingest/matcher.py`:

```python
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

        # Find VG candidates in same suburb, same type, within date window
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_matcher.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/tracker/ingest/matcher.py tests/test_matcher.py
git commit -m "feat: add address matcher for provisional -> VG linking"
```

---

### Task 4: CLI commands for Domain ingest and matching

**Files:**
- Modify: `src/tracker/cli.py` (add `ingest-domain` and `match-provisional` commands)
- No new tests needed (CLI integration tested via workflow)

**Step 1: Add the CLI commands**

Add imports at top of `src/tracker/cli.py`:

```python
from tracker.ingest.domain_sold import fetch_sold_listings
from tracker.ingest.matcher import match_provisional_to_vg
```

Add after the existing `ingest` command:

```python
@cli.command('ingest-domain')
@click.pass_context
def ingest_domain(ctx):
    """Fetch sold listings from Domain API for all segments."""
    import os
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    api_key = os.getenv('DOMAIN_API_KEY')
    if not api_key:
        click.echo("DOMAIN_API_KEY not set, skipping Domain ingest")
        return

    run_id = db.start_run('ingest-domain', 'cli')
    total_inserted = 0

    try:
        segments_config = config.get('segments', {})
        for seg_code, seg_def in segments_config.items():
            suburbs = seg_def.get('suburbs', [])
            prop_type = seg_def.get('property_type', 'house')
            # Look up postcode from existing sales data
            for suburb in suburbs:
                postcode_rows = db.query(
                    "SELECT DISTINCT postcode FROM raw_sales WHERE LOWER(suburb) = LOWER(?) LIMIT 1",
                    (suburb,)
                )
                postcode = postcode_rows[0]['postcode'] if postcode_rows else ''

                click.echo(f"Fetching Domain sold listings for {suburb} ({prop_type})...")
                listings = fetch_sold_listings(
                    suburb=suburb.title(),
                    property_type=prop_type,
                    postcode=postcode,
                    api_key=api_key,
                )

                if listings:
                    inserted = db.upsert_provisional_sales(listings)
                    total_inserted += inserted
                    click.echo(f"  {len(listings)} found, {inserted} new")
                else:
                    click.echo(f"  No sold listings found")

        click.echo(f"Total: {total_inserted} new provisional sales")
        db.complete_run(run_id, status='success', records_inserted=total_inserted)

    except Exception as e:
        logger.exception("Domain ingest failed")
        db.complete_run(run_id, status='failed', error_message=str(e))
        raise click.ClickException(f"Domain ingest failed: {e}")


@cli.command('match-provisional')
@click.pass_context
def match_provisional(ctx):
    """Match provisional Domain sales to VG records."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    click.echo("Matching provisional sales to VG records...")
    matched = match_provisional_to_vg(db)
    click.echo(f"Matched {matched} provisional sales")
```

**Step 2: Verify import works**

Run: `.venv/bin/python -m tracker --help`
Expected: Shows `ingest-domain` and `match-provisional` in command list

**Step 3: Commit**

```bash
git add src/tracker/cli.py
git commit -m "feat: add CLI commands for Domain ingest and provisional matching"
```

---

### Task 5: Report integration - show unconfirmed sales

**Files:**
- Modify: `src/tracker/notify/telegram.py` (update `format_simple_report`)
- Test: `tests/test_telegram.py` (add test for provisional section)

**Step 1: Write the failing test**

Add to `tests/test_telegram.py` (or create if minimal test exists):

```python
class TestProvisionalSalesInReport:
    """Test provisional sales section in report."""

    def test_includes_unconfirmed_sales_section(self):
        from tracker.notify.telegram import format_simple_report
        provisional = [
            {
                'unit_number': '9',
                'house_number': '27-29',
                'street_name': 'Morton St',
                'suburb': 'Wollstonecraft',
                'sold_price': 1200000,
                'sold_date': '2026-02-03',
                'property_type': 'unit',
            },
        ]
        report = format_simple_report(
            new_sales={},
            positions={},
            period='Feb 6, 2026',
            config={'report': {'show_proxies': []}},
            provisional_sales=provisional,
        )
        assert 'Recent Unconfirmed' in report
        assert 'Morton St' in report
        assert '1.2M' in report or '1,200,000' in report or '$1.20M' in report

    def test_no_section_when_empty(self):
        from tracker.notify.telegram import format_simple_report
        report = format_simple_report(
            new_sales={},
            positions={},
            period='Feb 6, 2026',
            config={'report': {'show_proxies': []}},
            provisional_sales=[],
        )
        assert 'Recent Unconfirmed' not in report
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_telegram.py::TestProvisionalSalesInReport -v`
Expected: FAIL (signature doesn't accept provisional_sales)

**Step 3: Implement the report section**

In `src/tracker/notify/telegram.py`, update `format_simple_report` signature to accept `provisional_sales`:

```python
def format_simple_report(
    new_sales: Dict[str, List],
    positions: Dict[str, SegmentPosition],
    period: str,
    config: Optional[dict] = None,
    provisional_sales: Optional[List[dict]] = None,
) -> str:
```

Add before the final `return "\n".join(lines)`:

```python
    # Section 3: Recent Unconfirmed Sales (Domain)
    if provisional_sales:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("<b>Recent Unconfirmed Sales (Domain)</b>")
        for sale in provisional_sales:
            unit = sale.get('unit_number', '')
            house = sale.get('house_number', '')
            street = sale.get('street_name', '')
            suburb = sale.get('suburb', '')
            price = sale.get('sold_price', 0)
            sold_date = sale.get('sold_date', '')

            addr_parts = []
            if unit:
                addr_parts.append(f"{unit}/")
            if house:
                addr_parts.append(f"{house} ")
            addr_parts.append(f"{street}, {suburb}")
            address = "".join(addr_parts)

            lines.append(f"  {sold_date}: {address} - {format_currency(price)}")
        lines.append("  <i>(Not in medians - awaiting VG confirmation)</i>")
```

Also update `send_simple_report` to pass through the provisional sales:

```python
def send_simple_report(
    config: TelegramConfig,
    new_sales: Dict[str, List],
    positions: Dict[str, SegmentPosition],
    period: str,
    app_config: Optional[dict] = None,
    provisional_sales: Optional[List[dict]] = None,
) -> bool:
    """Send the simplified report via Telegram (to report chat if configured)."""
    message = format_simple_report(new_sales, positions, period, app_config, provisional_sales)
    return send_message(config, message, use_report_chat=True)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_telegram.py::TestProvisionalSalesInReport -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS (existing report tests should still pass since `provisional_sales` defaults to `None`)

**Step 6: Commit**

```bash
git add src/tracker/notify/telegram.py tests/test_telegram.py
git commit -m "feat: show unconfirmed Domain sales in weekly report"
```

---

### Task 6: Wire up notify CLI to pass provisional sales

**Files:**
- Modify: `src/tracker/cli.py` (update `notify` command to query and pass provisional sales)

**Step 1: Find the notify command and update it**

In the `notify` command in `cli.py`, after loading metrics and before sending the report, add:

```python
        # Fetch unconfirmed provisional sales for report
        provisional_sales = db.get_unconfirmed_provisional_sales()
```

Then pass `provisional_sales=provisional_sales` to `send_simple_report()`.

**Step 2: Verify import and test**

Run: `.venv/bin/python -m tracker notify --dry-run`
Expected: Report prints including provisional section (if any provisional sales exist)

**Step 3: Commit**

```bash
git add src/tracker/cli.py
git commit -m "feat: pass provisional sales to report in notify command"
```

---

### Task 7: GitHub Actions workflow integration

**Files:**
- Modify: `.github/workflows/weekly-report.yml`

**Step 1: Add workflow steps**

After "Step 1 - Ingest new sales data", add:

```yaml
      - name: Step 1b - Ingest Domain sold listings
        env:
          DOMAIN_API_KEY: ${{ secrets.DOMAIN_API_KEY }}
        run: |
          python -m tracker ingest-domain

      - name: Step 1c - Match provisional sales to VG
        run: |
          python -m tracker match-provisional
```

**Step 2: Commit**

```bash
git add .github/workflows/weekly-report.yml
git commit -m "feat(ci): add Domain ingest and matching to weekly workflow"
```

---

### Task 8: Final integration test and full suite

**Step 1: Run complete test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS

**Step 2: Verify CLI commands work end-to-end**

Run:
```bash
.venv/bin/python -m tracker ingest-domain  # Should say "DOMAIN_API_KEY not set"
.venv/bin/python -m tracker match-provisional  # Should say "Matched 0 provisional sales"
```

**Step 3: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore: integration cleanup for Domain sold listings"
```
