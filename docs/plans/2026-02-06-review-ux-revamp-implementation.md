# Review UX Revamp — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the per-sale Telegram review messages with batched digest messages, replace the paid Domain API with Google search + LLM agent for sold listing discovery, and add price-withheld tracking.

**Architecture:** Google search scrapes sold listings from multiple real estate sites. An LLM agent extracts details from listing pages when snippets lack data. Telegram sends one digest per segment with clickable address links and a button grid for review. The Cloudflare Worker handles batched callbacks.

**Tech Stack:** Python 3.11, SQLite, Telegram Bot API (HTML mode + inline keyboards), Google search scraping (requests + BeautifulSoup), Anthropic Claude API (for LLM agent), Cloudflare Workers (JS).

**Design doc:** `docs/plans/2026-02-06-review-ux-revamp-design.md`

**Test runner:** `.venv/bin/python -m pytest tests/ -v`

---

## Task 1: DB Schema — Add `listing_url` and `price_withheld` Support

**Files:**
- Modify: `src/tracker/db.py:236-254` (sale_classifications table)
- Modify: `src/tracker/db.py:265-288` (provisional_sales table)
- Modify: `src/tracker/db.py:302-319` (_migrate_schema)
- Test: `tests/test_db_schema_migration.py` (new file)

**Step 1: Write failing test**

```python
# tests/test_db_schema_migration.py
import pytest
from tracker.db import Database

class TestSchemaRevampMigration:
    def test_sale_classifications_has_listing_url(self, db):
        """sale_classifications table should have listing_url column."""
        cols = {row[1] for row in db.query("PRAGMA table_info(sale_classifications)")}
        assert 'listing_url' in cols

    def test_provisional_sales_allows_price_withheld(self, db):
        """provisional_sales should accept 'price_withheld' status."""
        db.execute("""
            INSERT INTO provisional_sales (id, source, suburb, status)
            VALUES ('test-1', 'google', 'Revesby', 'price_withheld')
        """)
        rows = db.query("SELECT status FROM provisional_sales WHERE id = 'test-1'")
        assert rows[0]['status'] == 'price_withheld'

    def test_provisional_sales_has_listing_url(self, db):
        """provisional_sales should have listing_url column."""
        cols = {row[1] for row in db.query("PRAGMA table_info(provisional_sales)")}
        assert 'listing_url' in cols

    def test_provisional_sales_has_source_site(self, db):
        """provisional_sales should have source_site column."""
        cols = {row[1] for row in db.query("PRAGMA table_info(provisional_sales)")}
        assert 'source_site' in cols
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db_schema_migration.py -v`
Expected: FAIL — columns don't exist, CHECK constraint rejects 'price_withheld'

**Step 3: Implement schema changes**

In `src/tracker/db.py`:

1. Add `listing_url TEXT` column to the `sale_classifications` CREATE TABLE (after `review_notes`, line 249).

2. Update the `provisional_sales` CHECK constraint (line 283-284) to include `'price_withheld'`:
```python
CHECK(status IN ('unconfirmed', 'confirmed', 'superseded', 'price_withheld'))
```

3. Add `listing_url TEXT` and `source_site TEXT` columns to the `provisional_sales` CREATE TABLE (after `raw_json`, line 285).

4. In `_migrate_schema()` (line 302), add migrations for existing databases:
```python
# Migrate sale_classifications: add listing_url
if 'listing_url' not in sc_columns:
    conn.execute("ALTER TABLE sale_classifications ADD COLUMN listing_url TEXT")

# Migrate provisional_sales: add listing_url, source_site
if 'listing_url' not in ps_columns:
    conn.execute("ALTER TABLE provisional_sales ADD COLUMN listing_url TEXT")
if 'source_site' not in ps_columns:
    conn.execute("ALTER TABLE provisional_sales ADD COLUMN source_site TEXT")
```

Note: The CHECK constraint change only applies to new databases. For existing databases, SQLite doesn't enforce CHECK on ALTER TABLE, so `price_withheld` will work via the migration path. For a fresh DB the updated CREATE TABLE handles it.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_db_schema_migration.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All existing tests still pass

**Step 6: Commit**

```bash
git add src/tracker/db.py tests/test_db_schema_migration.py
git commit -m "feat(db): add listing_url, source_site columns and price_withheld status"
```

---

## Task 2: Google Search Scraper Module

**Files:**
- Create: `src/tracker/ingest/google_search.py`
- Test: `tests/test_google_search.py` (new file)

**Step 1: Write failing tests**

```python
# tests/test_google_search.py
import pytest
from unittest.mock import patch, MagicMock
from tracker.ingest.google_search import (
    build_search_query,
    parse_search_results_html,
    extract_listing_data,
    fetch_sold_listings_google,
    REAL_ESTATE_DOMAINS,
)


class TestBuildSearchQuery:
    def test_builds_revesby_query(self):
        query = build_search_query(suburb='Revesby', property_type='house')
        assert 'sold' in query.lower()
        assert 'Revesby' in query
        assert 'house' in query

    def test_builds_wollstonecraft_query_with_beds(self):
        query = build_search_query(
            suburb='Wollstonecraft', property_type='unit',
            bedrooms=2, bathrooms=1,
        )
        assert 'Wollstonecraft' in query
        assert '2 bed' in query


class TestParseSearchResultsHtml:
    def test_extracts_domain_listing(self):
        """Parse a Google search result page with a domain.com.au listing."""
        html = '''<div class="g">
            <a href="https://www.domain.com.au/15-alliance-ave-revesby-nsw-2212-abc123">
                <h3>15 Alliance Ave, Revesby NSW 2212 - Sold</h3>
            </a>
            <div class="VwiC3b">Sold for $1,420,000 on 3 Feb 2026. 3 bed, 2 bath, 2 car. 556sqm land.</div>
        </div>'''
        results = parse_search_results_html(html)
        assert len(results) >= 1
        assert 'domain.com.au' in results[0]['url']
        assert 'Alliance' in results[0]['title']

    def test_extracts_realestate_listing(self):
        """Parse a Google search result from realestate.com.au."""
        html = '''<div class="g">
            <a href="https://www.realestate.com.au/sold/property-house-nsw-revesby-12345">
                <h3>20 Smith St, Revesby NSW 2212</h3>
            </a>
            <div class="VwiC3b">Sold $1,380,000. 3 bedroom house.</div>
        </div>'''
        results = parse_search_results_html(html)
        assert len(results) >= 1
        assert 'realestate.com.au' in results[0]['url']

    def test_ignores_non_realestate_results(self):
        """Skip results not from real estate sites."""
        html = '''<div class="g">
            <a href="https://www.wikipedia.org/something">
                <h3>Some wiki page</h3>
            </a>
            <div class="VwiC3b">Not a listing</div>
        </div>'''
        results = parse_search_results_html(html)
        assert len(results) == 0


class TestExtractListingData:
    def test_extracts_price_from_snippet(self):
        result = {
            'url': 'https://www.domain.com.au/15-alliance-ave-abc123',
            'title': '15 Alliance Ave, Revesby NSW 2212 - Sold',
            'snippet': 'Sold for $1,420,000 on 3 Feb 2026. 3 bed, 2 bath, 2 car.',
        }
        data = extract_listing_data(result, suburb='Revesby', postcode='2212')
        assert data['sold_price'] == 1420000
        assert data['bedrooms'] == 3
        assert data['bathrooms'] == 2
        assert data['source_site'] == 'domain.com.au'

    def test_handles_price_withheld(self):
        result = {
            'url': 'https://www.domain.com.au/10-jones-ave-abc456',
            'title': '10 Jones Ave, Revesby NSW 2212',
            'snippet': 'Price withheld. 3 bed house.',
        }
        data = extract_listing_data(result, suburb='Revesby', postcode='2212')
        assert data['sold_price'] is None
        assert data['price_withheld'] is True

    def test_extracts_address_from_title(self):
        result = {
            'url': 'https://www.domain.com.au/5-10-shirley-rd-abc789',
            'title': '5/10 Shirley Rd, Wollstonecraft NSW 2065 - Sold',
            'snippet': 'Sold for $1,200,000.',
        }
        data = extract_listing_data(result, suburb='Wollstonecraft', postcode='2065')
        assert data['unit_number'] == '5'
        assert data['house_number'] == '10'
        assert 'Shirley' in data['street_name']


class TestFetchSoldListingsGoogle:
    @patch('tracker.ingest.google_search.requests.get')
    def test_returns_parsed_results(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '''<div class="g">
            <a href="https://www.domain.com.au/15-alliance-ave-revesby-nsw-2212-abc123">
                <h3>15 Alliance Ave, Revesby NSW 2212 - Sold</h3>
            </a>
            <div class="VwiC3b">Sold for $1,420,000. 556sqm.</div>
        </div>'''
        mock_get.return_value = mock_response

        results = fetch_sold_listings_google(
            suburb='Revesby', property_type='house', postcode='2212',
        )
        assert len(results) >= 1

    @patch('tracker.ingest.google_search.requests.get')
    def test_returns_empty_on_block(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_get.return_value = mock_response

        results = fetch_sold_listings_google(
            suburb='Revesby', property_type='house', postcode='2212',
        )
        assert results == []

    @patch('tracker.ingest.google_search.requests.get')
    def test_returns_empty_on_exception(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        results = fetch_sold_listings_google(
            suburb='Revesby', property_type='house', postcode='2212',
        )
        assert results == []
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_google_search.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement google_search.py**

```python
# src/tracker/ingest/google_search.py
"""Google search scraper for sold property listings.

Searches Google for recent sold listings across multiple real estate sites
(domain.com.au, realestate.com.au, allhomes.com.au, etc.) and extracts
structured data from search result snippets.
"""

import json
import logging
import random
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

from tracker.ingest.normalise import normalise_address

logger = logging.getLogger(__name__)

REAL_ESTATE_DOMAINS = [
    'domain.com.au',
    'realestate.com.au',
    'allhomes.com.au',
]

# Priority order for listing URL preference
DOMAIN_PRIORITY = {
    'domain.com.au': 1,
    'realestate.com.au': 2,
    'allhomes.com.au': 3,
}

USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

GOOGLE_SEARCH_URL = 'https://www.google.com/search'


def build_search_query(
    suburb: str,
    property_type: str,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[int] = None,
) -> str:
    """Build a Google search query for sold listings in a suburb."""
    parts = ['sold', suburb]

    if property_type == 'unit' and bedrooms:
        parts.append(f'{bedrooms} bed')
        if bathrooms:
            parts.append(f'{bathrooms} bath')
        parts.append('apartment')
    else:
        parts.append(property_type)

    return ' '.join(parts)


def parse_search_results_html(html: str) -> List[dict]:
    """Parse Google search results HTML and extract real estate listings.

    Returns list of dicts with keys: url, title, snippet, source_site
    """
    soup = BeautifulSoup(html, 'html.parser')
    results = []

    for g in soup.select('div.g'):
        link = g.select_one('a[href]')
        if not link:
            continue

        url = link.get('href', '')
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname or ''

        # Only keep results from real estate sites
        source_site = None
        for domain in REAL_ESTATE_DOMAINS:
            if domain in hostname:
                source_site = domain
                break

        if not source_site:
            continue

        title_el = g.select_one('h3')
        title = title_el.get_text(strip=True) if title_el else ''

        snippet_el = g.select_one('div.VwiC3b') or g.select_one('[data-sncf]')
        snippet = snippet_el.get_text(strip=True) if snippet_el else ''

        results.append({
            'url': url,
            'title': title,
            'snippet': snippet,
            'source_site': source_site,
        })

    return results


def extract_listing_data(
    result: dict,
    suburb: str,
    postcode: str,
) -> dict:
    """Extract structured listing data from a search result.

    Args:
        result: dict with url, title, snippet, source_site
        suburb: expected suburb
        postcode: expected postcode

    Returns:
        dict with: address fields, sold_price, bedrooms, bathrooms,
        car_spaces, listing_url, source_site, price_withheld
    """
    title = result.get('title', '')
    snippet = result.get('snippet', '')
    combined = f"{title} {snippet}"

    # Extract price
    price_match = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)\s*(?:m(?:illion)?)?', combined, re.IGNORECASE)
    sold_price = None
    price_withheld = False

    if price_match:
        price_str = price_match.group(1).replace(',', '')
        try:
            price_val = float(price_str)
            if price_val < 100:  # Likely in millions (e.g. $1.42m)
                sold_price = int(price_val * 1_000_000)
            else:
                sold_price = int(price_val)
        except ValueError:
            pass

    if sold_price is None:
        if re.search(r'price\s+withheld|undisclosed|contact\s+agent', combined, re.IGNORECASE):
            price_withheld = True

    # Extract bedrooms/bathrooms/car
    bed_match = re.search(r'(\d+)\s*bed', combined, re.IGNORECASE)
    bath_match = re.search(r'(\d+)\s*bath', combined, re.IGNORECASE)
    car_match = re.search(r'(\d+)\s*car', combined, re.IGNORECASE)

    bedrooms = int(bed_match.group(1)) if bed_match else None
    bathrooms = int(bath_match.group(1)) if bath_match else None
    car_spaces = int(car_match.group(1)) if car_match else None

    # Extract address from title
    # Titles typically: "15 Alliance Ave, Revesby NSW 2212 - Sold"
    # or "5/10 Shirley Rd, Wollstonecraft NSW 2065"
    address_part = re.split(r'\s*[-–—]\s*(?:Sold|House|Unit|Property)', title)[0].strip()
    # Remove suburb/state/postcode suffix
    address_part = re.sub(r',?\s*' + re.escape(suburb) + r'\s*(?:NSW)?\s*\d*\s*$', '', address_part, flags=re.IGNORECASE).strip()

    # Parse unit/house from address
    unit_number = None
    house_number = ''
    street_name = address_part

    # Handle "5/10 Shirley Rd" format
    unit_match = re.match(r'^(\d+[a-zA-Z]?)\s*/\s*(\d+[-\d]*)\s+(.+)$', address_part)
    if unit_match:
        unit_number = unit_match.group(1)
        house_number = unit_match.group(2)
        street_name = unit_match.group(3)
    else:
        # Handle "15 Alliance Ave" format
        house_match = re.match(r'^(\d+[-\d]*[a-zA-Z]?)\s+(.+)$', address_part)
        if house_match:
            house_number = house_match.group(1)
            street_name = house_match.group(2)

    # Extract area if present
    area_match = re.search(r'(\d+)\s*(?:sqm|m²|m2)', combined, re.IGNORECASE)
    area_sqm = float(area_match.group(1)) if area_match else None

    # Extract sold date if present
    date_match = re.search(r'(?:sold\s+(?:on\s+)?)?(\d{1,2}\s+\w+\s+\d{4})', combined, re.IGNORECASE)
    sold_date = date_match.group(1) if date_match else None

    address_normalised = normalise_address(
        unit_number=unit_number,
        house_number=house_number,
        street_name=street_name,
        suburb=suburb,
        postcode=postcode,
    )

    return {
        'unit_number': unit_number,
        'house_number': house_number,
        'street_name': street_name,
        'suburb': suburb,
        'postcode': postcode,
        'sold_price': sold_price,
        'sold_date': sold_date,
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'car_spaces': car_spaces,
        'area_sqm': area_sqm,
        'listing_url': result.get('url', ''),
        'source_site': result.get('source_site', ''),
        'address_normalised': address_normalised,
        'price_withheld': price_withheld,
    }


def _deduplicate_results(listings: List[dict]) -> List[dict]:
    """Deduplicate listings by normalised address.

    Prefers domain.com.au URLs, merges data across sources.
    """
    by_address = {}

    for listing in listings:
        addr = listing['address_normalised']
        if addr not in by_address:
            by_address[addr] = listing
        else:
            existing = by_address[addr]
            existing_priority = DOMAIN_PRIORITY.get(existing.get('source_site', ''), 99)
            new_priority = DOMAIN_PRIORITY.get(listing.get('source_site', ''), 99)

            # Prefer higher-priority source for URL
            if new_priority < existing_priority:
                existing['listing_url'] = listing['listing_url']
                existing['source_site'] = listing['source_site']

            # Merge missing fields from new result
            for field in ['sold_price', 'bedrooms', 'bathrooms', 'car_spaces', 'area_sqm', 'sold_date']:
                if existing.get(field) is None and listing.get(field) is not None:
                    existing[field] = listing[field]

    return list(by_address.values())


def fetch_sold_listings_google(
    suburb: str,
    property_type: str,
    postcode: str,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[int] = None,
) -> List[dict]:
    """Fetch sold listings via Google search.

    Returns list of parsed listing dicts, or empty list on error.
    """
    query = build_search_query(suburb, property_type, bedrooms, bathrooms)

    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-AU,en;q=0.9',
    }

    params = {
        'q': query,
        'num': 20,
        'gl': 'au',
        'hl': 'en',
    }

    try:
        delay = random.uniform(2.0, 5.0)
        time.sleep(delay)

        response = requests.get(
            GOOGLE_SEARCH_URL, headers=headers, params=params, timeout=30,
        )

        if response.status_code != 200:
            logger.warning(f"Google search returned {response.status_code} for '{query}'")
            return []

        raw_results = parse_search_results_html(response.text)
        listings = [
            extract_listing_data(r, suburb=suburb, postcode=postcode)
            for r in raw_results
        ]

        deduped = _deduplicate_results(listings)
        logger.info(f"Google search found {len(deduped)} sold {property_type}s in {suburb}")
        return deduped

    except requests.RequestException as e:
        logger.error(f"Google search failed: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error in Google search: {e}")
        return []
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_google_search.py -v`
Expected: PASS

Note: You may need to `pip install beautifulsoup4` if not already installed. Check with `.venv/bin/pip list | grep beautifulsoup`.

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tracker/ingest/google_search.py tests/test_google_search.py
git commit -m "feat(ingest): add Google search scraper for sold listings"
```

---

## Task 3: LLM Agent Fallback Module

**Files:**
- Create: `src/tracker/ingest/llm_agent.py`
- Test: `tests/test_llm_agent.py` (new file)

**Step 1: Write failing tests**

```python
# tests/test_llm_agent.py
import pytest
from unittest.mock import patch, MagicMock
from tracker.ingest.llm_agent import (
    extract_listing_details,
    fetch_page_content,
    build_extraction_prompt,
)


class TestBuildExtractionPrompt:
    def test_includes_address_context(self):
        prompt = build_extraction_prompt(
            page_text='Some listing text here',
            suburb='Revesby',
        )
        assert 'Revesby' in prompt
        assert 'price' in prompt.lower()
        assert 'JSON' in prompt

    def test_includes_page_text(self):
        prompt = build_extraction_prompt(
            page_text='Sold for $1.2M on 3 Feb 2026',
            suburb='Wollstonecraft',
        )
        assert '$1.2M' in prompt


class TestFetchPageContent:
    @patch('tracker.ingest.llm_agent.requests.get')
    def test_fetches_and_strips_html(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><body><p>Sold for $1,420,000</p></body></html>'
        mock_get.return_value = mock_response

        text = fetch_page_content('https://domain.com.au/listing-123')
        assert '$1,420,000' in text
        assert '<html>' not in text

    @patch('tracker.ingest.llm_agent.requests.get')
    def test_returns_none_on_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        text = fetch_page_content('https://domain.com.au/listing-123')
        assert text is None


class TestExtractListingDetails:
    @patch('tracker.ingest.llm_agent.fetch_page_content')
    @patch('tracker.ingest.llm_agent.call_llm')
    def test_returns_structured_data(self, mock_llm, mock_fetch):
        mock_fetch.return_value = 'Sold for $1,420,000. 3 bed 2 bath 2 car. Built 1965.'
        mock_llm.return_value = '{"price": 1420000, "bedrooms": 3, "bathrooms": 2, "car_spaces": 2, "year_built": 1965}'

        data = extract_listing_details(
            listing_url='https://domain.com.au/listing-123',
            suburb='Revesby',
            api_key='test-key',
        )
        assert data['price'] == 1420000
        assert data['bedrooms'] == 3
        assert data['year_built'] == 1965

    @patch('tracker.ingest.llm_agent.fetch_page_content')
    def test_returns_none_when_page_fails(self, mock_fetch):
        mock_fetch.return_value = None

        data = extract_listing_details(
            listing_url='https://domain.com.au/listing-123',
            suburb='Revesby',
            api_key='test-key',
        )
        assert data is None

    def test_returns_none_without_api_key(self):
        data = extract_listing_details(
            listing_url='https://domain.com.au/listing-123',
            suburb='Revesby',
            api_key=None,
        )
        assert data is None
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_agent.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement llm_agent.py**

```python
# src/tracker/ingest/llm_agent.py
"""LLM agent fallback for extracting listing details from property pages.

When Google search snippets lack detail (missing price, beds/baths),
this module fetches the listing page and uses an LLM to extract
structured property data.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_PAGE_TEXT_LENGTH = 8000  # Truncate page text to control LLM costs


def fetch_page_content(url: str) -> Optional[str]:
    """Fetch a listing page and return its text content (HTML stripped)."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'text/html',
    }
    try:
        time.sleep(1.0)
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch {url}: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        # Remove script/style elements
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
            tag.decompose()

        text = soup.get_text(separator='\n', strip=True)
        return text[:MAX_PAGE_TEXT_LENGTH]

    except Exception as e:
        logger.error(f"Failed to fetch listing page: {e}")
        return None


def build_extraction_prompt(page_text: str, suburb: str) -> str:
    """Build the LLM prompt for extracting property details."""
    return f"""Extract property details from this listing page text for a property in {suburb}.

Return a JSON object with these fields (use null if not found):
- price: integer (e.g. 1420000)
- bedrooms: integer
- bathrooms: integer
- car_spaces: integer
- year_built: integer
- land_area_sqm: integer
- property_description: string (brief, max 100 chars)

Return ONLY the JSON object, no other text.

Listing page text:
{page_text}"""


def call_llm(prompt: str, api_key: str) -> Optional[str]:
    """Call Claude API to extract listing details."""
    try:
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 300,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=30,
        )
        if response.status_code != 200:
            logger.warning(f"LLM API returned {response.status_code}")
            return None

        data = response.json()
        return data['content'][0]['text']

    except Exception as e:
        logger.error(f"LLM API call failed: {e}")
        return None


def extract_listing_details(
    listing_url: str,
    suburb: str,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Extract structured property details from a listing page using LLM.

    Args:
        listing_url: URL of the property listing page
        suburb: Expected suburb name
        api_key: Anthropic API key

    Returns:
        Dict with extracted details, or None on failure
    """
    if not api_key:
        logger.debug("No Anthropic API key, skipping LLM extraction")
        return None

    page_text = fetch_page_content(listing_url)
    if not page_text:
        return None

    prompt = build_extraction_prompt(page_text, suburb)
    result = call_llm(prompt, api_key)
    if not result:
        return None

    try:
        # Strip markdown code fences if present
        cleaned = result.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else cleaned[3:]
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        return json.loads(cleaned)
    except (json.JSONDecodeError, IndexError) as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        return None
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_llm_agent.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tracker/ingest/llm_agent.py tests/test_llm_agent.py
git commit -m "feat(ingest): add LLM agent fallback for listing detail extraction"
```

---

## Task 4: Update Ingest to Use Google Search + Price Withheld

**Files:**
- Modify: `src/tracker/ingest/domain_sold.py` (update `fetch_sold_listings` to try Google first)
- Modify: `src/tracker/db.py:437-467` (`upsert_provisional_sales` — handle new columns)
- Test: `tests/test_domain_sold.py` (update existing tests)

**Step 1: Write failing tests**

Add to `tests/test_domain_sold.py`:

```python
class TestGoogleSearchFallback:
    @patch('tracker.ingest.domain_sold.fetch_sold_listings_google')
    def test_uses_google_when_no_api_key(self, mock_google):
        mock_google.return_value = [{
            'unit_number': None,
            'house_number': '15',
            'street_name': 'Alliance Ave',
            'suburb': 'Revesby',
            'postcode': '2212',
            'sold_price': 1420000,
            'sold_date': '2026-02-03',
            'bedrooms': 3,
            'bathrooms': 2,
            'car_spaces': 2,
            'listing_url': 'https://www.domain.com.au/15-alliance-ave-abc123',
            'source_site': 'domain.com.au',
            'address_normalised': '|15|alliance ave|revesby|2212',
            'price_withheld': False,
            'property_type': 'house',
        }]

        results = fetch_sold_listings(
            suburb='Revesby', property_type='house', postcode='2212', api_key=None,
        )
        assert len(results) == 1
        assert results[0]['listing_url'] == 'https://www.domain.com.au/15-alliance-ave-abc123'
        mock_google.assert_called_once()

    @patch('tracker.ingest.domain_sold.fetch_sold_listings_google')
    def test_handles_price_withheld(self, mock_google):
        mock_google.return_value = [{
            'unit_number': None,
            'house_number': '10',
            'street_name': 'Jones Ave',
            'suburb': 'Revesby',
            'postcode': '2212',
            'sold_price': None,
            'sold_date': None,
            'bedrooms': 3,
            'bathrooms': None,
            'car_spaces': None,
            'listing_url': 'https://www.domain.com.au/10-jones-ave-abc456',
            'source_site': 'domain.com.au',
            'address_normalised': '|10|jones ave|revesby|2212',
            'price_withheld': True,
            'property_type': 'house',
        }]

        results = fetch_sold_listings(
            suburb='Revesby', property_type='house', postcode='2212', api_key=None,
        )
        assert len(results) == 1
        assert results[0]['sold_price'] is None
        assert results[0].get('status') == 'price_withheld'
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_sold.py::TestGoogleSearchFallback -v`
Expected: FAIL

**Step 3: Update domain_sold.py**

Modify `fetch_sold_listings()` to:
1. Always try Google search (it's free)
2. Only try Domain API as bonus if api_key is provided
3. Add `listing_url`, `source_site`, `status` fields to output
4. Set `status = 'price_withheld'` when price is None and price_withheld flag is True

Update the function signature to accept `bedrooms` and `bathrooms` parameters for the Google search query.

Also update `parse_sold_listing()` to include `listing_url`, `source_site`, and `status` fields in its output (defaulting to empty/None for Domain API results).

Update `upsert_provisional_sales()` in `db.py` to handle the new `listing_url`, `source_site` columns in the INSERT statement.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_domain_sold.py -v`
Expected: All pass (both old and new tests)

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tracker/ingest/domain_sold.py src/tracker/db.py tests/test_domain_sold.py
git commit -m "feat(ingest): use Google search as primary sold listing source"
```

---

## Task 5: Enrichment — Store Listing URL, Improve Error Labels

**Files:**
- Modify: `src/tracker/enrich/pipeline.py:17-56` (`enrich_sale` function)
- Modify: `src/tracker/enrich/pipeline.py:87-191` (`process_pending_sales` function)
- Test: `tests/test_enrich_pipeline.py` (update existing tests)

**Step 1: Write failing tests**

Add to `tests/test_enrich_pipeline.py`:

```python
class TestEnrichmentErrorLabels:
    @patch('tracker.enrich.pipeline.get_zoning')
    @patch('tracker.enrich.pipeline.get_year_built')
    def test_labels_unknown_year(self, mock_year, mock_zoning):
        mock_zoning.return_value = 'R2'
        mock_year.return_value = None

        enrichment = enrich_sale('15 Smith St', 'Revesby', '2212')
        assert enrichment['year_built'] is None
        assert enrichment['year_built_label'] == 'Year unknown'

    @patch('tracker.enrich.pipeline.get_zoning')
    def test_labels_unverified_zoning(self, mock_zoning):
        mock_zoning.return_value = None

        enrichment = enrich_sale('15 Smith St', 'Revesby', '2212')
        assert enrichment['zoning_label'] == 'Zoning unverified'
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_enrich_pipeline.py::TestEnrichmentErrorLabels -v`
Expected: FAIL

**Step 3: Implement changes**

In `enrich_sale()`:
1. Add `year_built_label` field: `"Built {year}"` if year_built, else `"Year unknown"`
2. Add `zoning_label` field: `zoning` if zoning, else `"Zoning unverified"`

In `process_pending_sales()`:
1. Add `listing_url` to the INSERT INTO `sale_classifications` — look up from `provisional_sales` if the sale was matched, otherwise construct a Google search fallback URL: `https://www.google.com/search?q={address}+{suburb}+sold`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_enrich_pipeline.py -v`
Expected: All pass

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tracker/enrich/pipeline.py tests/test_enrich_pipeline.py
git commit -m "feat(enrich): add error labels and listing_url to enrichment"
```

---

## Task 6: Telegram Digest Messages with Batched Buttons

**Files:**
- Modify: `src/tracker/notify/telegram.py` (add `send_review_digest()`, `format_review_digest()`)
- Test: `tests/test_telegram_digest.py` (new file)

**Step 1: Write failing tests**

```python
# tests/test_telegram_digest.py
import pytest
from unittest.mock import patch, MagicMock
from tracker.notify.telegram import (
    format_review_digest,
    build_digest_keyboard,
    send_review_digest,
    TelegramConfig,
)


class TestFormatReviewDigest:
    def test_formats_single_sale(self):
        sales = [{
            'sale_id': 'ABC123',
            'address': '15 Alliance Ave',
            'price': 1420000,
            'area_sqm': 556.0,
            'zoning_label': 'R2',
            'year_built_label': 'Built 1965',
            'listing_url': 'https://www.domain.com.au/15-alliance-ave-abc123',
        }]
        msg = format_review_digest('Revesby Houses', sales)
        assert '📋' in msg
        assert 'Revesby Houses' in msg
        assert '1 to review' in msg
        assert '<a href="https://www.domain.com.au/15-alliance-ave-abc123">15 Alliance Ave</a>' in msg
        assert '$1,420,000' in msg
        assert 'R2' in msg
        assert 'Built 1965' in msg

    def test_formats_multiple_sales(self):
        sales = [
            {
                'sale_id': 'ABC123',
                'address': '15 Alliance Ave',
                'price': 1420000,
                'area_sqm': 556.0,
                'zoning_label': 'R2',
                'year_built_label': 'Built 1965',
                'listing_url': 'https://www.domain.com.au/abc123',
            },
            {
                'sale_id': 'DEF456',
                'address': '20 Smith St',
                'price': 1380000,
                'area_sqm': 580.0,
                'zoning_label': 'R2',
                'year_built_label': 'Built 1972',
                'listing_url': 'https://www.domain.com.au/def456',
            },
        ]
        msg = format_review_digest('Revesby Houses', sales)
        assert '2 to review' in msg
        assert '1.' in msg
        assert '2.' in msg

    def test_shows_year_unknown(self):
        sales = [{
            'sale_id': 'X1',
            'address': '8 Jones Ave',
            'price': 1350000,
            'area_sqm': 520.0,
            'zoning_label': 'Zoning unverified',
            'year_built_label': 'Year unknown',
            'listing_url': 'https://www.google.com/search?q=8+Jones+Ave+Revesby+sold',
        }]
        msg = format_review_digest('Revesby Houses', sales)
        assert 'Year unknown' in msg
        assert 'Zoning unverified' in msg

    def test_uses_google_fallback_link(self):
        sales = [{
            'sale_id': 'X2',
            'address': '5 Test Rd',
            'price': 1000000,
            'area_sqm': None,
            'zoning_label': 'R2',
            'year_built_label': 'Built 2000',
            'listing_url': None,
        }]
        msg = format_review_digest('Revesby Houses', sales)
        assert 'google.com/search' in msg


class TestBuildDigestKeyboard:
    def test_one_row_per_sale_plus_bulk(self):
        sale_ids = [('ABC123', 'seg1'), ('DEF456', 'seg1')]
        keyboard = build_digest_keyboard(sale_ids, 'seg1')
        # 2 sale rows + 1 bulk row = 3 rows
        assert len(keyboard['inline_keyboard']) == 3
        # Each sale row has 2 buttons (yes/no)
        assert len(keyboard['inline_keyboard'][0]) == 2
        # Bulk row has 2 buttons
        assert len(keyboard['inline_keyboard'][2]) == 2

    def test_callback_data_format(self):
        sale_ids = [('ABC123', 'seg1')]
        keyboard = build_digest_keyboard(sale_ids, 'seg1')
        yes_btn = keyboard['inline_keyboard'][0][0]
        no_btn = keyboard['inline_keyboard'][0][1]
        assert yes_btn['callback_data'] == 'review:seg1:ABC123:yes'
        assert no_btn['callback_data'] == 'review:seg1:ABC123:no'

    def test_bulk_callback_data(self):
        sale_ids = [('ABC123', 'seg1'), ('DEF456', 'seg1')]
        keyboard = build_digest_keyboard(sale_ids, 'seg1')
        bulk_row = keyboard['inline_keyboard'][2]
        assert 'all' in bulk_row[0]['callback_data']
        assert 'all' in bulk_row[1]['callback_data']

    def test_max_5_sales_per_keyboard(self):
        sale_ids = [(f'SALE{i}', 'seg1') for i in range(7)]
        keyboard = build_digest_keyboard(sale_ids[:5], 'seg1')
        # 5 sale rows + 1 bulk row = 6 rows
        assert len(keyboard['inline_keyboard']) == 6


class TestSendReviewDigest:
    @patch('tracker.notify.telegram.requests.post')
    def test_sends_message_with_keyboard(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        mock_post.return_value = mock_response

        config = TelegramConfig(bot_token='token', chat_id='123')
        sales = [{
            'sale_id': 'ABC123',
            'address': '15 Alliance Ave',
            'price': 1420000,
            'area_sqm': 556.0,
            'zoning_label': 'R2',
            'year_built_label': 'Built 1965',
            'listing_url': 'https://www.domain.com.au/abc123',
        }]
        result = send_review_digest(config, 'Revesby Houses', sales, 'revesby_houses')
        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]['json'] if 'json' in call_kwargs[1] else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
        # Verify it was called with reply_markup containing inline_keyboard
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_telegram_digest.py -v`
Expected: FAIL — functions don't exist

**Step 3: Implement digest functions**

Add to `src/tracker/notify/telegram.py`:

1. `format_review_digest(segment_name: str, sales: List[dict]) -> str` — builds the HTML message with clickable address links. If `listing_url` is None, constructs a Google search fallback.

2. `build_digest_keyboard(sale_ids: List[Tuple[str, str]], segment_code: str) -> dict` — builds the inline keyboard with one row per sale (`[N ✅] [N ❌]`) plus a bulk row (`[All ✅] [All ❌]`).

3. `send_review_digest(config: TelegramConfig, segment_name: str, sales: List[dict], segment_code: str) -> bool` — sends the digest message with keyboard to the personal chat. Returns True on success.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_telegram_digest.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tracker/notify/telegram.py tests/test_telegram_digest.py
git commit -m "feat(telegram): add batched digest review messages with clickable links"
```

---

## Task 7: Update Cloudflare Worker for Batched Callbacks

**Files:**
- Modify: `webhook/worker.js`

**Step 1: Plan the changes**

The worker needs to handle:
1. Individual callbacks: `review:{segment}:{sale_id}:{yes|no}` (same as before)
2. Bulk callbacks: `review:{segment}:all:{yes|no}` — triggers multiple DB updates
3. After individual callback: edit the message to mark that sale's verdict in the text and remove its button row (keep other rows)
4. After bulk callback: mark all sales and remove all buttons

**Step 2: Implement changes**

Update `webhook/worker.js`:

1. Parse callback data — detect `all` as special sale_id
2. For individual:
   - Acknowledge tap
   - Edit message text: add verdict marker (✓/✗) to the relevant line
   - Edit keyboard: remove that sale's button row, keep others and bulk row
   - Trigger GitHub dispatch for that sale
3. For bulk (`all`):
   - Acknowledge tap
   - Edit message text: add verdict markers to all lines
   - Remove all buttons
   - Trigger GitHub dispatch for each sale (extract sale IDs from the remaining keyboard buttons)

Key challenge: the worker needs to know which sale IDs are in the message to handle "all". The keyboard buttons contain the sale IDs in their callback_data, so parse them from `message.reply_markup.inline_keyboard`.

The message editing uses `editMessageText` with updated text and `editMessageReplyMarkup` with the modified keyboard.

**Step 3: Write the updated worker.js**

Replace the existing worker with the new version that handles both individual and bulk callbacks. Keep the same authentication and GitHub dispatch patterns.

**Step 4: Test manually**

Deploy to Cloudflare with `wrangler deploy` and test by tapping buttons in Telegram.

**Step 5: Commit**

```bash
git add webhook/worker.js
git commit -m "feat(webhook): handle batched digest callbacks with individual and bulk actions"
```

---

## Task 8: Update CLI `review-buttons` Command

**Files:**
- Modify: `src/tracker/cli.py:986-1069` (`review_buttons` command)

**Step 1: Write failing test**

Add to a new test file or update existing:

```python
# tests/test_cli_review.py
import pytest
from unittest.mock import patch, MagicMock, call
from click.testing import CliRunner
from tracker.cli import cli


class TestReviewButtonsDigest:
    @patch('tracker.cli.send_review_digest')
    @patch('tracker.cli.Database')
    @patch('tracker.cli.load_config')
    @patch('tracker.cli.init_segments')
    def test_sends_digest_per_segment(self, mock_init_seg, mock_config, mock_db_cls, mock_send):
        """review-buttons should send one digest per segment, not individual messages."""
        mock_config.return_value = {'segments': {}}
        mock_db = MagicMock()
        mock_db_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_db_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Return 2 pending sales for one segment
        mock_db.query.return_value = [
            {'sale_id': 'A1', 'address': '15 Smith St', 'zoning': 'R2',
             'year_built': 1965, 'listing_url': 'https://domain.com.au/a1'},
            {'sale_id': 'A2', 'address': '20 Jones Ave', 'zoning': 'R2',
             'year_built': None, 'listing_url': None},
        ]
        mock_send.return_value = True

        runner = CliRunner()
        result = runner.invoke(cli, ['review-buttons', '--segment', 'revesby_houses'])

        # Should call send_review_digest once (not send_review_with_buttons twice)
        mock_send.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_review.py -v`
Expected: FAIL

**Step 3: Implement changes**

Update the `review-buttons` command in `cli.py`:

1. Query pending sales grouped by segment (same query as before but batch them)
2. For each segment with pending sales:
   - Build the sales list with `listing_url`, `zoning_label`, `year_built_label`
   - Split into chunks of max 5 sales
   - Call `send_review_digest()` for each chunk
   - Update `review_sent_at` for all sent sales
3. Remove the loop that calls `send_review_with_buttons()` individually

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_review.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tracker/cli.py tests/test_cli_review.py
git commit -m "feat(cli): update review-buttons to send batched digest messages"
```

---

## Task 9: Update GitHub Workflows

**Files:**
- Modify: `.github/workflows/weekly-report.yml`
- Modify: `.github/workflows/review-poll.yml`

**Step 1: Update weekly-report.yml**

1. Replace the `ingest-domain` step (line ~190) to not require `DOMAIN_API_KEY`:
   - The Google search scraper doesn't need an API key
   - Keep the command but update the CLI to use Google search when no Domain key

2. Add `ANTHROPIC_API_KEY` secret for the LLM agent fallback step

3. Update the `review-buttons` step (line ~250) — no changes needed to the command, the CLI handles the batched digest internally

**Step 2: Update review-poll.yml**

The Cloudflare Worker now handles callbacks immediately. The `review-poll.yml` workflow is still useful as a backup for processing any callbacks the worker missed (e.g. if worker was down).

Update the inline Python script to handle the `all` case — when a bulk callback is received, update all matching sales for that segment.

**Step 3: Commit**

```bash
git add .github/workflows/weekly-report.yml .github/workflows/review-poll.yml
git commit -m "feat(ci): update workflows for Google search ingest and batched reviews"
```

---

## Task 10: Add `ingest-google` CLI Command

**Files:**
- Modify: `src/tracker/cli.py` (add new command)

**Step 1: Write failing test**

```python
# tests/test_cli_ingest_google.py
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from tracker.cli import cli

class TestIngestGoogleCommand:
    @patch('tracker.cli.fetch_sold_listings_google')
    @patch('tracker.cli.Database')
    @patch('tracker.cli.load_config')
    @patch('tracker.cli.init_segments')
    def test_ingests_google_results(self, mock_init_seg, mock_config, mock_db_cls, mock_fetch):
        mock_config.return_value = {'segments': {
            'revesby_houses': {
                'suburbs': ['revesby'], 'property_type': 'house', 'postcodes': ['2212'],
            }
        }}
        mock_db = MagicMock()
        mock_db_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_db_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.upsert_provisional_sales.return_value = 1
        mock_fetch.return_value = [{'id': 'google-1', 'source': 'google'}]

        runner = CliRunner()
        result = runner.invoke(cli, ['ingest-google'])
        assert result.exit_code == 0
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_ingest_google.py -v`
Expected: FAIL — command doesn't exist

**Step 3: Implement**

Add `ingest-google` CLI command that:
1. Iterates over segments with `require_manual_review=True`
2. Calls `fetch_sold_listings_google()` for each segment
3. Converts results to provisional_sales format (adding `id = f"google-{hash}"`, `source = 'google'`)
4. Calls `db.upsert_provisional_sales()` with the new `listing_url` and `source_site` fields
5. Optionally runs LLM agent for results with missing price/details (if `--enrich` flag passed and `ANTHROPIC_API_KEY` set)

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_ingest_google.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tracker/cli.py tests/test_cli_ingest_google.py
git commit -m "feat(cli): add ingest-google command for Google search sold listings"
```

---

## Task 11: Integration Test & Cleanup

**Files:**
- Review all modified files
- Run full test suite
- Remove dead code

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 2: Check for dead code**

Verify these are still needed or can be removed:
- `send_review_with_buttons()` in `telegram.py` — remove if no longer called
- The text-based review flow in `review/telegram.py` — keep as backup
- Domain API imports in `domain_sold.py` — remove if Google search fully replaces it
- `review-poll` CLI command — keep as backup for worker failures

**Step 3: Run linting/formatting if configured**

Run: `.venv/bin/python -m pytest tests/ -v` one final time

**Step 4: Commit cleanup**

```bash
git add -A
git commit -m "refactor: remove dead Domain API code, clean up imports"
```

---

## Dependencies

Check if `beautifulsoup4` is in requirements:

```bash
grep beautifulsoup requirements.txt || echo "NEED TO ADD"
```

If not present:
```bash
echo "beautifulsoup4>=4.12" >> requirements.txt
pip install beautifulsoup4
git add requirements.txt
git commit -m "deps: add beautifulsoup4 for Google search HTML parsing"
```

Also check if `anthropic` SDK is needed (we use raw `requests` in the plan, so no).
