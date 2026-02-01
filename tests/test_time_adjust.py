# tests/test_time_adjust.py
"""Tests for time-adjusted median calculation."""

import pytest
from datetime import date
from tracker.compute.time_adjust import (
    calculate_months_ago,
    apply_time_adjustment,
    calculate_recency_weight,
    weighted_median,
    compute_time_adjusted_median,
    get_adjusted_sales_detail,
)


class TestCalculateMonthsAgo:
    """Test months ago calculation."""

    def test_same_month(self):
        """Same month returns 0."""
        assert calculate_months_ago(date(2025, 6, 15), date(2025, 6, 1)) == 0

    def test_one_month_ago(self):
        """One month difference."""
        assert calculate_months_ago(date(2025, 5, 15), date(2025, 6, 1)) == 1

    def test_year_ago(self):
        """12 months difference."""
        assert calculate_months_ago(date(2024, 6, 15), date(2025, 6, 1)) == 12

    def test_14_months_ago(self):
        """14 months difference."""
        assert calculate_months_ago(date(2024, 4, 15), date(2025, 6, 1)) == 14


class TestApplyTimeAdjustment:
    """Test time adjustment calculation."""

    def test_no_adjustment_for_current(self):
        """No adjustment for 0 months ago."""
        adjusted, pct = apply_time_adjustment(1000000, 0, 0.07)
        assert adjusted == 1000000
        assert pct == 0

    def test_12_month_adjustment_7pct(self):
        """12 months at 7% should be ~7% higher."""
        adjusted, pct = apply_time_adjustment(1000000, 12, 0.07)
        assert 1060000 < adjusted < 1080000  # ~7% growth
        assert 6.5 < pct < 7.5

    def test_6_month_adjustment(self):
        """6 months should be ~half of annual growth."""
        adjusted, pct = apply_time_adjustment(1000000, 6, 0.07)
        assert 1030000 < adjusted < 1040000  # ~3.5% growth
        assert 3.0 < pct < 4.0


class TestRecencyWeight:
    """Test recency weighting."""

    def test_current_month_weight_1(self):
        """Current month has weight 1."""
        assert calculate_recency_weight(0) == 1.0

    def test_older_has_lower_weight(self):
        """Older sales have lower weight."""
        w1 = calculate_recency_weight(1)
        w6 = calculate_recency_weight(6)
        w12 = calculate_recency_weight(12)
        assert w1 > w6 > w12

    def test_weight_decay(self):
        """Weight at 10 months is about half."""
        w10 = calculate_recency_weight(10, decay_factor=0.1)
        assert 0.45 < w10 < 0.55


class TestWeightedMedian:
    """Test weighted median calculation."""

    def test_equal_weights(self):
        """Equal weights should give regular median."""
        values = [(100, 1), (200, 1), (300, 1)]
        assert weighted_median(values) == 200

    def test_higher_weight_on_low(self):
        """Higher weight on lower value pulls median down."""
        values = [(100, 10), (200, 1), (300, 1)]
        assert weighted_median(values) == 100

    def test_higher_weight_on_high(self):
        """Higher weight on higher value pulls median up."""
        values = [(100, 1), (200, 1), (300, 10)]
        assert weighted_median(values) == 300

    def test_empty_list(self):
        """Empty list returns 0."""
        assert weighted_median([]) == 0


class TestComputeTimeAdjustedMedian:
    """Test full time-adjusted median calculation."""

    def test_empty_sales(self):
        """Empty sales returns zeros."""
        result = compute_time_adjusted_median([])
        assert result.sample_size == 0
        assert result.naive_median == 0
        assert result.adjusted_median == 0

    def test_single_sale(self):
        """Single sale returns that sale's adjusted value."""
        sales = [{
            'sale_id': 'S1',
            'address': '1 Test St',
            'purchase_price': 1500000,
            'contract_date': '2025-01-15',
        }]
        result = compute_time_adjusted_median(
            sales,
            reference_date=date(2026, 2, 1),
            base_growth_rate=0.07,
        )
        assert result.sample_size == 1
        assert result.naive_median == 1500000
        # Adjusted should be higher (about 13 months of growth)
        assert result.adjusted_median > 1500000
        assert result.adjusted_median < 1700000

    def test_multiple_sales(self):
        """Multiple sales calculates proper median."""
        sales = [
            {'sale_id': 'S1', 'address': '1 St', 'purchase_price': 1400000, 'contract_date': '2025-06-01'},
            {'sale_id': 'S2', 'address': '2 St', 'purchase_price': 1500000, 'contract_date': '2025-09-01'},
            {'sale_id': 'S3', 'address': '3 St', 'purchase_price': 1600000, 'contract_date': '2025-12-01'},
        ]
        result = compute_time_adjusted_median(
            sales,
            reference_date=date(2026, 2, 1),
            base_growth_rate=0.07,
        )
        assert result.sample_size == 3
        assert result.oldest_sale_date == date(2025, 6, 1)
        assert result.newest_sale_date == date(2025, 12, 1)

    def test_scenarios_ordering(self):
        """Conservative < base < optimistic."""
        sales = [
            {'sale_id': 'S1', 'address': '1 St', 'purchase_price': 1400000, 'contract_date': '2025-01-01'},
            {'sale_id': 'S2', 'address': '2 St', 'purchase_price': 1500000, 'contract_date': '2025-06-01'},
        ]
        result = compute_time_adjusted_median(
            sales,
            reference_date=date(2026, 2, 1),
        )
        assert result.conservative_median <= result.weighted_median <= result.optimistic_median

    def test_sales_by_month(self):
        """Sales by month is populated correctly."""
        sales = [
            {'sale_id': 'S1', 'address': '1 St', 'purchase_price': 1400000, 'contract_date': '2025-06-01'},
            {'sale_id': 'S2', 'address': '2 St', 'purchase_price': 1500000, 'contract_date': '2025-06-15'},
            {'sale_id': 'S3', 'address': '3 St', 'purchase_price': 1600000, 'contract_date': '2025-07-01'},
        ]
        result = compute_time_adjusted_median(sales)
        assert result.sales_by_month.get('2025-06') == 2
        assert result.sales_by_month.get('2025-07') == 1


class TestGetAdjustedSalesDetail:
    """Test detailed adjustment info."""

    def test_returns_adjusted_sales(self):
        """Returns list of AdjustedSale objects."""
        sales = [
            {'sale_id': 'S1', 'address': '1 Test St', 'purchase_price': 1500000, 'contract_date': '2025-06-01'},
        ]
        result = get_adjusted_sales_detail(
            sales,
            reference_date=date(2026, 2, 1),
            growth_rate=0.07,
        )
        assert len(result) == 1
        assert result[0].sale_id == 'S1'
        assert result[0].original_price == 1500000
        assert result[0].adjusted_price > 1500000
        assert result[0].months_ago == 8
