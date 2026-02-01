# Comparables Review System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add buyer's-agent-level comparable selection for Revesby with auto-enrichment and Telegram review workflow.

**Architecture:** New `sale_classifications` table tracks enrichment data and review status. Auto-enrichment pipeline queries NSW Planning Portal (zoning) and Domain API (year built). Telegram flow sends pending sales for review, user replies with tags, system updates `use_in_median` flag. Metrics calculation filters to only approved comparables.

**Tech Stack:** Python 3.9+, SQLite, requests (HTTP), pytest

---

## Task 1: Add sale_classifications Table

**Files:**
- Modify: `src/tracker/db.py:66-235` (init_schema method)
- Test: `tests/test_db.py` (create new)

**Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
# tests/test_db.py
"""Tests for database operations."""

import pytest
import tempfile
import os
from tracker.db import Database


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(db_path=path)
    db.init_schema()
    yield db
    db.close()
    os.unlink(path)


class TestSaleClassificationsTable:
    """Test sale_classifications table exists and works."""

    def test_table_exists(self, temp_db):
        """sale_classifications table is created."""
        tables = temp_db.list_tables()
        assert 'sale_classifications' in tables

    def test_insert_classification(self, temp_db):
        """Can insert and query a classification."""
        temp_db.execute("""
            INSERT INTO sale_classifications (
                sale_id, address, zoning, year_built, has_duplex_keywords,
                is_auto_excluded, auto_exclude_reason, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ('sale_123', '15 Smith St Revesby', 'R2', 1965, False, False, None, 'pending'))

        rows = temp_db.query("SELECT * FROM sale_classifications WHERE sale_id = ?", ('sale_123',))
        assert len(rows) == 1
        assert rows[0]['address'] == '15 Smith St Revesby'
        assert rows[0]['zoning'] == 'R2'
        assert rows[0]['year_built'] == 1965
        assert rows[0]['review_status'] == 'pending'
        assert rows[0]['use_in_median'] == 0  # Default FALSE
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_db.py -v`
Expected: FAIL with "sale_classifications" not found

**Step 3: Add sale_classifications table to init_schema**

In `src/tracker/db.py`, add after line 205 (after review_queue index creation):

```python
        # 7. sale_classifications - Comparable review tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sale_classifications (
                sale_id TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                zoning TEXT,
                year_built INTEGER,
                has_duplex_keywords BOOLEAN DEFAULT FALSE,
                is_auto_excluded BOOLEAN DEFAULT FALSE,
                auto_exclude_reason TEXT,
                review_status TEXT DEFAULT 'pending'
                    CHECK(review_status IN ('pending', 'comparable', 'not_comparable')),
                reviewed_at TIMESTAMP,
                review_notes TEXT,
                use_in_median BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sale_classifications_status
            ON sale_classifications(review_status, is_auto_excluded)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sale_classifications_median
            ON sale_classifications(use_in_median)
        """)
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_db.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/db.py tests/test_db.py
git commit -m "feat(db): add sale_classifications table for comparable review"
```

---

## Task 2: Keyword Scanner

**Files:**
- Create: `src/tracker/enrich/classifier.py`
- Test: `tests/test_classifier.py`

**Step 1: Write the failing test**

Create `tests/test_classifier.py`:

```python
# tests/test_classifier.py
"""Tests for sale classification logic."""

import pytest
from tracker.enrich.classifier import (
    has_exclude_keywords,
    should_auto_exclude,
    EXCLUDE_KEYWORDS,
)


class TestKeywordScanner:
    """Test keyword detection for auto-exclusion."""

    def test_detects_duplex(self):
        """Detects 'duplex' keyword."""
        assert has_exclude_keywords("Brand new duplex on corner block") is True

    def test_detects_dual_occ(self):
        """Detects 'dual occ' keyword."""
        assert has_exclude_keywords("Dual occ approved site") is True

    def test_detects_torrens(self):
        """Detects 'torrens' keyword (title type for duplexes)."""
        assert has_exclude_keywords("Torrens title half") is True

    def test_detects_brand_new(self):
        """Detects 'brand new' keyword."""
        assert has_exclude_keywords("Brand new 4 bedroom home") is True

    def test_detects_just_completed(self):
        """Detects 'just completed' keyword."""
        assert has_exclude_keywords("Just completed modern residence") is True

    def test_case_insensitive(self):
        """Keywords detected regardless of case."""
        assert has_exclude_keywords("DUPLEX opportunity") is True
        assert has_exclude_keywords("Dual OCC potential") is True

    def test_no_keywords_returns_false(self):
        """Returns False when no keywords present."""
        assert has_exclude_keywords("Original fibro home on 556sqm") is False
        assert has_exclude_keywords("3 bedroom house in quiet street") is False

    def test_empty_string(self):
        """Handles empty string."""
        assert has_exclude_keywords("") is False

    def test_none_description(self):
        """Handles None description."""
        assert has_exclude_keywords(None) is False


class TestAutoExcludeDecision:
    """Test combined auto-exclude logic."""

    def test_excludes_non_r2_zoning(self):
        """Excludes properties not zoned R2 or R3."""
        excluded, reason = should_auto_exclude(zoning='B1', year_built=1970, has_keywords=False)
        assert excluded is True
        assert 'non-R2' in reason

    def test_allows_r2_zoning(self):
        """Allows R2 zoning."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=1970, has_keywords=False)
        assert excluded is False

    def test_allows_r3_zoning(self):
        """Allows R3 zoning (medium density)."""
        excluded, reason = should_auto_exclude(zoning='R3', year_built=1970, has_keywords=False)
        assert excluded is False

    def test_excludes_modern_build(self):
        """Excludes properties built after 2010."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=2018, has_keywords=False)
        assert excluded is True
        assert '2018' in reason

    def test_allows_old_build(self):
        """Allows properties built before 2010."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=1965, has_keywords=False)
        assert excluded is False

    def test_excludes_duplex_keywords(self):
        """Excludes when duplex keywords present."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=1970, has_keywords=True)
        assert excluded is True
        assert 'duplex' in reason.lower()

    def test_handles_none_values(self):
        """Handles None for zoning and year_built."""
        excluded, reason = should_auto_exclude(zoning=None, year_built=None, has_keywords=False)
        assert excluded is False  # Can't exclude without data
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_classifier.py -v`
Expected: FAIL with import error

**Step 3: Implement classifier.py**

Create `src/tracker/enrich/classifier.py`:

```python
# src/tracker/enrich/classifier.py
"""Classification logic for auto-excluding non-comparable sales."""

from typing import Optional, Tuple

# Keywords that indicate property is not a comparable (already developed/modern)
EXCLUDE_KEYWORDS = [
    'duplex',
    'dual occ',
    'torrens',
    'brand new',
    'just completed',
]

# Allowed zoning codes for duplex development potential
ALLOWED_ZONINGS = {'R2', 'R3'}

# Year built threshold - exclude modern builds
YEAR_BUILT_THRESHOLD = 2010


def has_exclude_keywords(description: Optional[str]) -> bool:
    """
    Check if description contains keywords indicating non-comparable.

    Args:
        description: Property description text (can be None)

    Returns:
        True if any exclude keywords found
    """
    if not description:
        return False

    desc_lower = description.lower()
    return any(kw in desc_lower for kw in EXCLUDE_KEYWORDS)


def should_auto_exclude(
    zoning: Optional[str],
    year_built: Optional[int],
    has_keywords: bool,
) -> Tuple[bool, Optional[str]]:
    """
    Determine if a sale should be auto-excluded from comparables.

    Args:
        zoning: Property zoning code (R2, R3, B1, etc.)
        year_built: Year property was built
        has_keywords: Whether exclude keywords were found in description

    Returns:
        Tuple of (is_excluded, reason)
        - is_excluded: True if should be auto-excluded
        - reason: Human-readable reason for exclusion, or None if not excluded
    """
    # Check zoning (if known)
    if zoning and zoning not in ALLOWED_ZONINGS:
        return True, f"non-R2 zoning ({zoning})"

    # Check year built (if known)
    if year_built and year_built > YEAR_BUILT_THRESHOLD:
        return True, f"modern build ({year_built})"

    # Check keywords
    if has_keywords:
        return True, "existing duplex"

    return False, None
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_classifier.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/enrich/classifier.py tests/test_classifier.py
git commit -m "feat(enrich): add keyword scanner and auto-exclude logic"
```

---

## Task 3: Domain API Client (Year Built Lookup)

**Files:**
- Create: `src/tracker/enrich/domain.py`
- Test: `tests/test_domain.py`

**Step 1: Write the failing test**

Create `tests/test_domain.py`:

```python
# tests/test_domain.py
"""Tests for Domain API client."""

import pytest
from unittest.mock import patch, Mock
from tracker.enrich.domain import (
    get_year_built,
    build_domain_search_url,
    parse_year_built_from_response,
)


class TestBuildSearchUrl:
    """Test URL building for Domain API."""

    def test_builds_url_with_address(self):
        """Builds correct search URL."""
        url = build_domain_search_url("15 Smith St", "Revesby", "2212")
        assert "domain.com.au" in url or "api.domain.com.au" in url
        assert "Smith" in url or "smith" in url.lower()


class TestParseYearBuilt:
    """Test parsing year built from API response."""

    def test_parses_year_from_response(self):
        """Extracts year built from API response."""
        response = {
            "propertyDetails": {
                "yearBuilt": 1965
            }
        }
        assert parse_year_built_from_response(response) == 1965

    def test_returns_none_when_missing(self):
        """Returns None when year not in response."""
        response = {"propertyDetails": {}}
        assert parse_year_built_from_response(response) is None

    def test_handles_empty_response(self):
        """Handles empty response."""
        assert parse_year_built_from_response({}) is None
        assert parse_year_built_from_response(None) is None


class TestGetYearBuilt:
    """Test full year built lookup."""

    @patch('tracker.enrich.domain.requests.get')
    def test_returns_year_on_success(self, mock_get):
        """Returns year built when API call succeeds."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "propertyDetails": {"yearBuilt": 1972}
        }
        mock_get.return_value = mock_response

        year = get_year_built("15 Smith St", "Revesby", "2212", api_key="test_key")
        assert year == 1972

    @patch('tracker.enrich.domain.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        """Returns None when API returns error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        year = get_year_built("15 Smith St", "Revesby", "2212", api_key="test_key")
        assert year is None

    def test_returns_none_without_api_key(self):
        """Returns None when no API key configured."""
        year = get_year_built("15 Smith St", "Revesby", "2212", api_key=None)
        assert year is None
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_domain.py -v`
Expected: FAIL with import error

**Step 3: Implement domain.py**

Create `src/tracker/enrich/domain.py`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_domain.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/enrich/domain.py tests/test_domain.py
git commit -m "feat(enrich): add Domain API client for year built lookup"
```

---

## Task 4: NSW Planning Portal Client (Zoning Lookup)

**Files:**
- Create: `src/tracker/enrich/zoning.py`
- Test: `tests/test_zoning.py`

**Step 1: Write the failing test**

Create `tests/test_zoning.py`:

```python
# tests/test_zoning.py
"""Tests for NSW Planning Portal zoning lookup."""

import pytest
from unittest.mock import patch, Mock
from tracker.enrich.zoning import (
    get_zoning,
    parse_zoning_from_response,
    build_zoning_url,
)


class TestParseZoning:
    """Test parsing zoning from API response."""

    def test_parses_r2_zoning(self):
        """Extracts R2 zoning code."""
        response = {
            "zoning": {
                "zoneName": "R2 Low Density Residential"
            }
        }
        assert parse_zoning_from_response(response) == "R2"

    def test_parses_r3_zoning(self):
        """Extracts R3 zoning code."""
        response = {
            "zoning": {
                "zoneName": "R3 Medium Density Residential"
            }
        }
        assert parse_zoning_from_response(response) == "R3"

    def test_parses_b1_zoning(self):
        """Extracts B1 zoning code."""
        response = {
            "zoning": {
                "zoneName": "B1 Neighbourhood Centre"
            }
        }
        assert parse_zoning_from_response(response) == "B1"

    def test_returns_none_when_missing(self):
        """Returns None when zoning not in response."""
        assert parse_zoning_from_response({}) is None
        assert parse_zoning_from_response(None) is None


class TestGetZoning:
    """Test full zoning lookup."""

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_zoning_on_success(self, mock_get):
        """Returns zoning when API call succeeds."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "zoning": {"zoneName": "R2 Low Density Residential"}
        }
        mock_get.return_value = mock_response

        zoning = get_zoning("15 Smith St, Revesby NSW 2212")
        assert zoning == "R2"

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        """Returns None when API returns error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        zoning = get_zoning("15 Smith St, Revesby NSW 2212")
        assert zoning is None
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_zoning.py -v`
Expected: FAIL with import error

**Step 3: Implement zoning.py**

Create `src/tracker/enrich/zoning.py`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_zoning.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/enrich/zoning.py tests/test_zoning.py
git commit -m "feat(enrich): add NSW Planning Portal client for zoning lookup"
```

---

## Task 5: Enrichment Pipeline Orchestrator

**Files:**
- Create: `src/tracker/enrich/pipeline.py`
- Test: `tests/test_enrich_pipeline.py`

**Step 1: Write the failing test**

Create `tests/test_enrich_pipeline.py`:

```python
# tests/test_enrich_pipeline.py
"""Tests for enrichment pipeline orchestrator."""

import pytest
from unittest.mock import patch, Mock, MagicMock
import tempfile
import os

from tracker.db import Database
from tracker.enrich.pipeline import (
    enrich_sale,
    classify_sale,
    process_pending_sales,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(db_path=path)
    db.init_schema()
    # Insert a test sale
    db.execute("""
        INSERT INTO raw_sales (
            dealing_number, property_id, street_name, suburb, postcode,
            area_sqm, contract_date, purchase_price, property_type, district_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ('DN123', '', 'Smith St', 'Revesby', '2212', 556, '2025-01-15', 1420000, 'house', 108))
    yield db
    db.close()
    os.unlink(path)


class TestEnrichSale:
    """Test individual sale enrichment."""

    @patch('tracker.enrich.pipeline.get_zoning')
    @patch('tracker.enrich.pipeline.get_year_built')
    def test_enriches_with_zoning_and_year(self, mock_year, mock_zoning):
        """Enriches sale with zoning and year built."""
        mock_zoning.return_value = 'R2'
        mock_year.return_value = 1965

        result = enrich_sale(
            address="15 Smith St, Revesby NSW 2212",
            suburb="Revesby",
            postcode="2212",
            description=None,
            api_key="test_key",
        )

        assert result['zoning'] == 'R2'
        assert result['year_built'] == 1965
        assert result['has_duplex_keywords'] is False

    def test_detects_keywords_in_description(self):
        """Detects duplex keywords in description."""
        result = enrich_sale(
            address="15 Smith St, Revesby NSW 2212",
            suburb="Revesby",
            postcode="2212",
            description="Brand new duplex",
            api_key=None,
        )

        assert result['has_duplex_keywords'] is True


class TestClassifySale:
    """Test sale classification logic."""

    def test_auto_excludes_non_r2(self):
        """Auto-excludes non-R2 zoning."""
        enrichment = {
            'zoning': 'B1',
            'year_built': 1970,
            'has_duplex_keywords': False,
        }

        result = classify_sale(enrichment)

        assert result['is_auto_excluded'] is True
        assert 'non-R2' in result['auto_exclude_reason']
        assert result['use_in_median'] is False

    def test_marks_pending_for_valid_sale(self):
        """Marks valid sale as pending review."""
        enrichment = {
            'zoning': 'R2',
            'year_built': 1965,
            'has_duplex_keywords': False,
        }

        result = classify_sale(enrichment)

        assert result['is_auto_excluded'] is False
        assert result['review_status'] == 'pending'
        assert result['use_in_median'] is False  # Until manually approved
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_enrich_pipeline.py -v`
Expected: FAIL with import error

**Step 3: Implement pipeline.py**

Create `src/tracker/enrich/pipeline.py`:

```python
# src/tracker/enrich/pipeline.py
"""Enrichment pipeline for sale classification."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tracker.db import Database
from tracker.enrich.classifier import has_exclude_keywords, should_auto_exclude
from tracker.enrich.domain import get_year_built
from tracker.enrich.zoning import get_zoning

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
        'review_status': 'pending' if not is_excluded else 'pending',
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
    # Find unclassified sales for this segment
    # For now, we match by suburb - segment filtering happens at query time
    query = """
        SELECT r.id, r.dealing_number, r.house_number, r.street_name,
               r.suburb, r.postcode, r.area_sqm
        FROM raw_sales r
        LEFT JOIN sale_classifications sc ON r.dealing_number = sc.sale_id
        WHERE sc.sale_id IS NULL
          AND LOWER(r.suburb) IN ('revesby', 'revesby heights')
          AND r.property_type = 'house'
          AND r.area_sqm BETWEEN 500 AND 600
        ORDER BY r.contract_date DESC
        LIMIT ?
    """

    sales = db.query(query, (limit,))
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
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_enrich_pipeline.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/enrich/pipeline.py tests/test_enrich_pipeline.py
git commit -m "feat(enrich): add enrichment pipeline orchestrator"
```

---

## Task 6: Telegram Review Message Formatting

**Files:**
- Create: `src/tracker/review/telegram.py`
- Test: `tests/test_review_telegram.py`

**Step 1: Write the failing test**

Create `tests/test_review_telegram.py`:

```python
# tests/test_review_telegram.py
"""Tests for Telegram review message formatting and parsing."""

import pytest
from tracker.review.telegram import (
    format_review_message,
    parse_review_reply,
    format_domain_url,
)


class TestFormatDomainUrl:
    """Test Domain URL generation."""

    def test_formats_url_correctly(self):
        """Generates correct Domain search URL."""
        url = format_domain_url("15 Smith St", "Revesby")
        assert "domain.com.au" in url
        assert "smith" in url.lower() or "revesby" in url.lower()


class TestFormatReviewMessage:
    """Test review message formatting."""

    def test_formats_single_sale(self):
        """Formats single sale for review."""
        sales = [{
            'sale_id': 'DN123',
            'address': '15 Smith St, Revesby',
            'price': 1420000,
            'area_sqm': 556,
            'zoning': 'R2',
            'year_built': 1965,
        }]

        message = format_review_message(sales)

        assert '15 Smith St' in message
        assert '$1,420,000' in message
        assert '556' in message
        assert 'R2' in message
        assert '1965' in message
        assert '1.' in message  # Numbered

    def test_formats_multiple_sales(self):
        """Formats multiple sales with numbers."""
        sales = [
            {'sale_id': 'DN1', 'address': '15 Smith St', 'price': 1400000, 'area_sqm': 550, 'zoning': 'R2', 'year_built': 1965},
            {'sale_id': 'DN2', 'address': '20 Jones Ave', 'price': 1500000, 'area_sqm': 580, 'zoning': 'R2', 'year_built': 1970},
        ]

        message = format_review_message(sales)

        assert '1.' in message
        assert '2.' in message
        assert '15 Smith St' in message
        assert '20 Jones Ave' in message

    def test_includes_reply_instructions(self):
        """Includes instructions for replying."""
        sales = [{'sale_id': 'DN1', 'address': '15 Smith St', 'price': 1400000, 'area_sqm': 550, 'zoning': 'R2', 'year_built': None}]

        message = format_review_message(sales)

        assert 'Reply' in message or 'reply' in message


class TestParseReviewReply:
    """Test reply parsing."""

    def test_parses_shorthand_emojis(self):
        """Parses shorthand emoji replies."""
        result = parse_review_reply("✅✅❌", sale_count=3)
        assert result == ['comparable', 'comparable', 'not_comparable']

    def test_parses_numbered_replies(self):
        """Parses numbered replies."""
        result = parse_review_reply("1✅ 2❌ 3✅", sale_count=3)
        assert result == ['comparable', 'not_comparable', 'comparable']

    def test_parses_all_approve(self):
        """Parses 'all' shortcut."""
        result = parse_review_reply("all✅", sale_count=3)
        assert result == ['comparable', 'comparable', 'comparable']

    def test_parses_skip(self):
        """Parses skip command."""
        result = parse_review_reply("skip", sale_count=3)
        assert result == ['pending', 'pending', 'pending']

    def test_handles_mixed_case(self):
        """Handles mixed case input."""
        result = parse_review_reply("ALL✅", sale_count=2)
        assert result == ['comparable', 'comparable']

    def test_handles_spaces(self):
        """Handles spaces between emojis."""
        result = parse_review_reply("✅ ✅ ❌", sale_count=3)
        assert result == ['comparable', 'comparable', 'not_comparable']

    def test_returns_none_for_invalid(self):
        """Returns None for invalid/incomplete replies."""
        result = parse_review_reply("✅", sale_count=3)  # Only 1 of 3
        assert result is None

        result = parse_review_reply("hello", sale_count=3)
        assert result is None
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_review_telegram.py -v`
Expected: FAIL with import error

**Step 3: Implement review/telegram.py**

Create `src/tracker/review/telegram.py`:

```python
# src/tracker/review/telegram.py
"""Telegram review message formatting and reply parsing."""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote


def format_currency(amount: int) -> str:
    """Format amount as currency string."""
    return f"${amount:,}"


def format_domain_url(address: str, suburb: str) -> str:
    """
    Generate Domain.com.au search URL for a property.

    Args:
        address: Street address (e.g., "15 Smith St")
        suburb: Suburb name

    Returns:
        Domain search URL
    """
    # Clean and encode address for URL
    search_term = f"{address} {suburb} NSW".lower().replace(' ', '-')
    return f"https://www.domain.com.au/{quote(search_term)}"


def format_review_message(sales: List[Dict[str, Any]]) -> str:
    """
    Format sales for Telegram review message.

    Args:
        sales: List of sale dicts with keys:
            - sale_id, address, price, area_sqm, zoning, year_built

    Returns:
        Formatted Telegram message (HTML)
    """
    lines = [f"📋 {len(sales)} Revesby sale{'s' if len(sales) != 1 else ''} need review:\n"]

    for i, sale in enumerate(sales, 1):
        address = sale.get('address', 'Unknown')
        price = format_currency(sale.get('price', 0))
        area = sale.get('area_sqm', 0)
        zoning = sale.get('zoning', 'Unknown')
        year = sale.get('year_built')

        # Format year info
        year_str = f"Built {year}" if year else "Year unknown"

        # Build Domain URL
        # Extract street part for URL
        street_part = address.split(',')[0] if ',' in address else address
        suburb = 'Revesby'
        domain_url = format_domain_url(street_part, suburb)

        lines.append(f"{i}. <b>{street_part}</b> - {price} ({area:.0f}sqm)")
        lines.append(f"   🏷️ {zoning} | {year_str}")
        lines.append(f"   🔗 {domain_url}")
        lines.append("")

    # Add reply instructions
    lines.append("Reply: <code>1✅ 2✅ 3❌</code>")
    lines.append("(or just <code>✅✅❌</code> or <code>all✅</code>)")

    return "\n".join(lines)


def parse_review_reply(text: str, sale_count: int) -> Optional[List[str]]:
    """
    Parse user reply into list of statuses.

    Args:
        text: User's reply text
        sale_count: Number of sales being reviewed

    Returns:
        List of statuses ['comparable', 'not_comparable', 'pending', ...]
        Returns None if reply is invalid/incomplete
    """
    text = text.strip().lower()

    # Handle special commands
    if text == 'skip':
        return ['pending'] * sale_count

    if text.startswith('all'):
        if '✅' in text:
            return ['comparable'] * sale_count
        if '❌' in text:
            return ['not_comparable'] * sale_count

    # Extract emojis in order
    statuses = []

    # Try numbered format first: "1✅ 2❌ 3✅"
    numbered_pattern = re.findall(r'(\d+)\s*([✅❌])', text)
    if numbered_pattern:
        # Build ordered list from numbered responses
        result = ['pending'] * sale_count
        for num_str, emoji in numbered_pattern:
            idx = int(num_str) - 1
            if 0 <= idx < sale_count:
                result[idx] = 'comparable' if emoji == '✅' else 'not_comparable'

        # Check all were specified
        if result.count('pending') == 0:
            return result

    # Try simple emoji sequence: "✅✅❌"
    emojis = re.findall(r'[✅❌]', text)
    if len(emojis) == sale_count:
        return [
            'comparable' if e == '✅' else 'not_comparable'
            for e in emojis
        ]

    # Invalid reply
    return None
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_review_telegram.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/review/telegram.py tests/test_review_telegram.py
git commit -m "feat(review): add Telegram review message formatting and parsing"
```

---

## Task 7: Review Status Update Handler

**Files:**
- Modify: `src/tracker/review/telegram.py`
- Test: `tests/test_review_telegram.py` (add tests)

**Step 1: Add failing test**

Add to `tests/test_review_telegram.py`:

```python
class TestUpdateReviewStatuses:
    """Test updating review statuses in database."""

    def test_updates_statuses(self, temp_db):
        """Updates review statuses in database."""
        # Insert test classifications
        temp_db.execute("""
            INSERT INTO sale_classifications (sale_id, address, review_status, use_in_median)
            VALUES ('DN1', '15 Smith St', 'pending', 0),
                   ('DN2', '20 Jones Ave', 'pending', 0),
                   ('DN3', '25 Brown Rd', 'pending', 0)
        """)

        from tracker.review.telegram import update_review_statuses

        sale_ids = ['DN1', 'DN2', 'DN3']
        statuses = ['comparable', 'not_comparable', 'comparable']

        updated = update_review_statuses(temp_db, sale_ids, statuses)

        assert updated == 3

        # Check database
        rows = temp_db.query("SELECT * FROM sale_classifications ORDER BY sale_id")
        assert rows[0]['review_status'] == 'comparable'
        assert rows[0]['use_in_median'] == 1
        assert rows[1]['review_status'] == 'not_comparable'
        assert rows[1]['use_in_median'] == 0
        assert rows[2]['review_status'] == 'comparable'
        assert rows[2]['use_in_median'] == 1
```

Also add fixture at top:

```python
@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    import tempfile
    import os
    from tracker.db import Database

    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(db_path=path)
    db.init_schema()
    yield db
    db.close()
    os.unlink(path)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_review_telegram.py::TestUpdateReviewStatuses -v`
Expected: FAIL with import error

**Step 3: Implement update_review_statuses**

Add to `src/tracker/review/telegram.py`:

```python
from datetime import datetime, timezone
from tracker.db import Database


def update_review_statuses(
    db: Database,
    sale_ids: List[str],
    statuses: List[str],
) -> int:
    """
    Update review statuses for sales in database.

    Args:
        db: Database connection
        sale_ids: List of sale IDs to update
        statuses: Corresponding list of statuses ('comparable', 'not_comparable', 'pending')

    Returns:
        Number of records updated
    """
    if len(sale_ids) != len(statuses):
        raise ValueError("sale_ids and statuses must have same length")

    updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for sale_id, status in zip(sale_ids, statuses):
        # Determine use_in_median based on status
        use_in_median = 1 if status == 'comparable' else 0

        result = db.execute("""
            UPDATE sale_classifications
            SET review_status = ?,
                use_in_median = ?,
                reviewed_at = ?,
                updated_at = ?
            WHERE sale_id = ?
        """, (status, use_in_median, now, now, sale_id))

        if result > 0:
            updated += 1

    return updated
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_review_telegram.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/review/telegram.py tests/test_review_telegram.py
git commit -m "feat(review): add review status update handler"
```

---

## Task 8: Integrate Review Filter into Metrics

**Files:**
- Modify: `src/tracker/compute/metrics.py`
- Test: `tests/test_metrics_review.py` (create new)

**Step 1: Write the failing test**

Create `tests/test_metrics_review.py`:

```python
# tests/test_metrics_review.py
"""Tests for metrics integration with review filter."""

import pytest
import tempfile
import os
from datetime import date

from tracker.db import Database
from tracker.compute.segments import init_segments
from tracker.compute.metrics import get_period_sales, get_verified_sales_count


@pytest.fixture
def db_with_classified_sales():
    """Database with sales and classifications."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(db_path=path)
    db.init_schema()

    # Initialize segments with default config
    init_segments({})

    # Insert test sales in Revesby
    sales_data = [
        ('DN1', '15', 'Smith St', 'Revesby', '2212', 550, '2025-01-10', 1400000, 'house', 108),
        ('DN2', '20', 'Jones Ave', 'Revesby', '2212', 560, '2025-01-15', 1500000, 'house', 108),
        ('DN3', '25', 'Brown Rd', 'Revesby', '2212', 570, '2025-01-20', 1600000, 'house', 108),
    ]

    for dn, house, street, suburb, postcode, area, contract, price, ptype, district in sales_data:
        db.execute("""
            INSERT INTO raw_sales (
                dealing_number, property_id, house_number, street_name, suburb, postcode,
                area_sqm, contract_date, purchase_price, property_type, district_code
            ) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (dn, house, street, suburb, postcode, area, contract, price, ptype, district))

    # Classify sales - DN1 and DN3 approved, DN2 rejected
    db.execute("""
        INSERT INTO sale_classifications (sale_id, address, review_status, use_in_median)
        VALUES ('DN1', '15 Smith St', 'comparable', 1),
               ('DN2', '20 Jones Ave', 'not_comparable', 0),
               ('DN3', '25 Brown Rd', 'comparable', 1)
    """)

    yield db
    db.close()
    os.unlink(path)


class TestGetVerifiedSalesCount:
    """Test getting count of verified comparables."""

    def test_counts_verified_sales(self, db_with_classified_sales):
        """Returns count of use_in_median=True sales."""
        count = get_verified_sales_count(db_with_classified_sales, 'revesby_houses')
        assert count == 2  # DN1 and DN3


class TestFilteredMetrics:
    """Test that metrics use only verified sales when segment requires review."""

    def test_excludes_rejected_sales(self, db_with_classified_sales):
        """Median excludes sales marked not_comparable."""
        # This test verifies the filtering logic
        # When require_manual_review is True, only use_in_median=True sales count
        prices = get_period_sales(
            db_with_classified_sales,
            'revesby_houses',
            date(2025, 1, 1),
            date(2025, 1, 31),
            use_verified_only=True,
        )

        # Should only get DN1 ($1.4M) and DN3 ($1.6M), not DN2 ($1.5M)
        assert len(prices) == 2
        assert 1400000 in prices
        assert 1600000 in prices
        assert 1500000 not in prices
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_metrics_review.py -v`
Expected: FAIL with import error or AttributeError

**Step 3: Add review filtering to metrics.py**

Add these functions to `src/tracker/compute/metrics.py`:

```python
def get_verified_sales_count(db: Database, segment_code: str) -> int:
    """
    Get count of verified comparable sales for a segment.

    Args:
        db: Database connection
        segment_code: Segment to count

    Returns:
        Number of sales with use_in_median=True
    """
    segment = get_segment(segment_code)
    if not segment:
        return 0

    suburbs = list(segment.suburbs)
    placeholders = ','.join(['?' for _ in suburbs])

    query = f"""
        SELECT COUNT(*) as count
        FROM raw_sales r
        JOIN sale_classifications sc ON r.dealing_number = sc.sale_id
        WHERE LOWER(r.suburb) IN ({placeholders})
          AND r.property_type = ?
          AND sc.use_in_median = 1
    """

    params = list(suburbs) + [segment.property_type]
    rows = db.query(query, tuple(params))
    return rows[0]['count'] if rows else 0
```

Also modify `get_period_sales` function signature and add filtering:

```python
def get_period_sales(
    db: Database,
    segment_code: str,
    start_date: date,
    end_date: date,
    use_verified_only: bool = False,
) -> List[int]:
    """
    Get sale prices for a segment within a date range.

    Args:
        db: Database connection
        segment_code: Segment to query
        start_date: Period start (inclusive)
        end_date: Period end (inclusive)
        use_verified_only: If True, only include sales with use_in_median=True

    Returns:
        List of sale prices
    """
    segment = get_segment(segment_code)
    if not segment:
        return []

    suburbs = list(segment.suburbs)
    placeholders = ','.join(['?' for _ in suburbs])

    # Base query
    if use_verified_only:
        query = f"""
            SELECT r.purchase_price
            FROM raw_sales r
            JOIN sale_classifications sc ON r.dealing_number = sc.sale_id
            WHERE LOWER(r.suburb) IN ({placeholders})
              AND r.property_type = ?
              AND r.contract_date BETWEEN ? AND ?
              AND r.purchase_price > 0
              AND sc.use_in_median = 1
        """
    else:
        query = f"""
            SELECT purchase_price
            FROM raw_sales
            WHERE LOWER(suburb) IN ({placeholders})
              AND property_type = ?
              AND contract_date BETWEEN ? AND ?
              AND purchase_price > 0
        """

    params: List = list(suburbs) + [segment.property_type, start_date.isoformat(), end_date.isoformat()]

    # Add area filter if specified
    if segment.area_min is not None:
        if use_verified_only:
            query += " AND r.area_sqm >= ?"
        else:
            query += " AND area_sqm >= ?"
        params.append(segment.area_min)
    if segment.area_max is not None:
        if use_verified_only:
            query += " AND r.area_sqm <= ?"
        else:
            query += " AND area_sqm <= ?"
        params.append(segment.area_max)

    # Add street filter if specified
    if segment.streets:
        street_list = list(segment.streets)
        street_placeholders = ','.join(['?' for _ in street_list])
        if use_verified_only:
            query += f" AND LOWER(r.street_name) IN ({street_placeholders})"
        else:
            query += f" AND LOWER(street_name) IN ({street_placeholders})"
        params.extend(street_list)

    rows = db.query(query, tuple(params))

    if use_verified_only:
        return [row['purchase_price'] for row in rows]
    else:
        return [row['purchase_price'] for row in rows]
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker && python -m pytest tests/test_metrics_review.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/compute/metrics.py tests/test_metrics_review.py
git commit -m "feat(metrics): integrate review filter for verified comparables"
```

---

## Task 9: Config Schema Update

**Files:**
- Modify: `config.yml.example`

**Step 1: Update config.yml.example**

Add to `config.yml.example` after the existing segments section:

```yaml
# ===================
# ENRICHMENT
# ===================
enrichment:
  domain_api_key: ${DOMAIN_API_KEY}  # Optional: Domain API key for year built lookup

# Update revesby_houses segment to add review config:
segments:
  revesby_houses:
    display_name: "Revesby Houses (IP)"
    suburbs: [revesby, revesby heights]
    property_type: house
    role: proxy
    filters:
      area_min: 500
      area_max: 600
    description: "500-600sqm land, comparable to your IP"
    # Comparables review settings
    require_manual_review: true
    auto_exclude:
      non_r2_zoning: true
      year_built_after: 2010
      keywords: [duplex, dual occ, torrens, brand new, just completed]
```

**Step 2: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add config.yml.example
git commit -m "docs(config): add enrichment and review config options"
```

---

## Task 10: CLI Commands for Enrichment and Review

**Files:**
- Modify: `src/tracker/cli.py`
- Test: Manual CLI testing

**Step 1: Add enrich command to CLI**

Add to `src/tracker/cli.py`:

```python
@click.command()
@click.option('--segment', default='revesby_houses', help='Segment to enrich')
@click.option('--limit', default=50, help='Max sales to process')
@click.option('--api-key', envvar='DOMAIN_API_KEY', help='Domain API key')
@click.pass_context
def enrich(ctx, segment, limit, api_key):
    """Enrich and classify sales for comparable review."""
    from tracker.enrich.pipeline import process_pending_sales

    db = ctx.obj['db']
    processed = process_pending_sales(db, segment, api_key=api_key, limit=limit)
    click.echo(f"Processed {processed} sales for {segment}")


@click.command()
@click.option('--segment', default='revesby_houses', help='Segment to show pending')
@click.pass_context
def pending(ctx, segment):
    """Show sales pending review."""
    db = ctx.obj['db']

    rows = db.query("""
        SELECT sc.sale_id, sc.address, sc.zoning, sc.year_built, r.purchase_price, r.area_sqm
        FROM sale_classifications sc
        JOIN raw_sales r ON sc.sale_id = r.dealing_number
        WHERE sc.review_status = 'pending'
          AND sc.is_auto_excluded = 0
        ORDER BY r.contract_date DESC
        LIMIT 20
    """)

    if not rows:
        click.echo("No sales pending review")
        return

    click.echo(f"{len(rows)} sales pending review:\n")
    for i, row in enumerate(rows, 1):
        click.echo(f"{i}. {row['address']}")
        click.echo(f"   ${row['purchase_price']:,} | {row['area_sqm']:.0f}sqm | {row['zoning'] or 'Unknown'} | {row['year_built'] or 'Year unknown'}")
        click.echo()


# Add commands to CLI group
cli.add_command(enrich)
cli.add_command(pending)
```

**Step 2: Test manually**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
python -m tracker enrich --limit 5
python -m tracker pending
```

**Step 3: Commit**

```bash
cd /Users/henrysit/Desktop/Vibecoding\ Projects/propertytracker
git add src/tracker/cli.py
git commit -m "feat(cli): add enrich and pending commands"
```

---

## Summary

This plan implements the Comparables Review System in 10 tasks:

1. **sale_classifications table** - Database schema
2. **Keyword scanner** - Detect duplex/modern keywords
3. **Domain API client** - Year built lookup
4. **NSW Planning Portal client** - Zoning lookup
5. **Enrichment pipeline** - Orchestrate enrichment
6. **Telegram review formatting** - Message format + parsing
7. **Review status handler** - Update DB from replies
8. **Metrics integration** - Filter by verified comparables
9. **Config schema** - Add enrichment config options
10. **CLI commands** - enrich and pending commands

Each task follows TDD with bite-sized steps and frequent commits.
