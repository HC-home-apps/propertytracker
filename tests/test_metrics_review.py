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
