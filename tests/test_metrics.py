# tests/test_metrics.py
"""Tests for metrics computation module."""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from tracker.compute.metrics import (
    compute_median,
    compute_yoy_change,
    compute_outpacing_metrics,
    MetricResult,
)


class TestComputeMedian:
    """Test median computation."""

    def test_simple_median(self):
        """Compute median of odd-length list."""
        assert compute_median([100, 200, 300]) == 200

    def test_even_length_list(self):
        """Compute median of even-length list."""
        assert compute_median([100, 200, 300, 400]) == 250

    def test_single_value(self):
        """Single value returns itself."""
        assert compute_median([500000]) == 500000

    def test_empty_returns_none(self):
        """Empty list returns None."""
        assert compute_median([]) is None

    def test_unsorted_list(self):
        """Handles unsorted list."""
        assert compute_median([300, 100, 200]) == 200

    def test_returns_integer(self):
        """Returns integer, not float."""
        result = compute_median([100, 200])
        assert isinstance(result, int)


class TestComputeYoyChange:
    """Test year-over-year percentage change."""

    def test_positive_change(self):
        """Positive YoY change."""
        assert compute_yoy_change(1100, 1000) == 10.0

    def test_negative_change(self):
        """Negative YoY change."""
        assert compute_yoy_change(900, 1000) == -10.0

    def test_no_change(self):
        """Zero change."""
        assert compute_yoy_change(1000, 1000) == 0.0

    def test_rounds_to_one_decimal(self):
        """Rounds to one decimal place."""
        result = compute_yoy_change(1033, 1000)
        assert result == 3.3

    def test_none_current_returns_none(self):
        """None current value returns None."""
        assert compute_yoy_change(None, 1000) is None

    def test_none_prior_returns_none(self):
        """None prior value returns None."""
        assert compute_yoy_change(1000, None) is None

    def test_zero_prior_returns_none(self):
        """Zero prior value returns None."""
        assert compute_yoy_change(1000, 0) is None


class TestMetricResult:
    """Test MetricResult dataclass."""

    def test_creates_valid_result(self):
        """Create valid MetricResult."""
        result = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=1500000,
            sample_size=10,
            yoy_pct=5.2,
            rolling_median_3m=1480000,
            rolling_sample_3m=25,
            is_suppressed=False,
            suppression_reason=None,
        )

        assert result.segment == 'revesby_houses'
        assert result.median_price == 1500000
        assert result.yoy_pct == 5.2

    def test_suppressed_result(self):
        """Create suppressed MetricResult."""
        result = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=None,
            sample_size=2,
            yoy_pct=None,
            rolling_median_3m=None,
            rolling_sample_3m=None,
            is_suppressed=True,
            suppression_reason='Insufficient sample size: 2 < 8',
        )

        assert result.is_suppressed is True
        assert 'Insufficient' in result.suppression_reason


class TestComputeOutpacingMetrics:
    """Test outpacing metric computation."""

    def test_positive_outpacing(self):
        """Proxy outpacing target."""
        proxy = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=1500000,
            sample_size=10,
            yoy_pct=8.0,
            rolling_median_3m=1480000,
            rolling_sample_3m=25,
        )

        target = MetricResult(
            segment='lane_cove_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=2500000,
            sample_size=15,
            yoy_pct=5.0,
            rolling_median_3m=2450000,
            rolling_sample_3m=30,
        )

        result = compute_outpacing_metrics(proxy, target)

        assert result['pct_spread'] == 3.0  # 8.0 - 5.0
        assert result['is_outpacing'] is True
        assert result['proxy_yoy'] == 8.0
        assert result['target_yoy'] == 5.0

    def test_negative_outpacing(self):
        """Target outpacing proxy."""
        proxy = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=1500000,
            sample_size=10,
            yoy_pct=3.0,
            rolling_median_3m=1480000,
            rolling_sample_3m=25,
        )

        target = MetricResult(
            segment='lane_cove_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=2500000,
            sample_size=15,
            yoy_pct=7.0,
            rolling_median_3m=2450000,
            rolling_sample_3m=30,
        )

        result = compute_outpacing_metrics(proxy, target)

        assert result['pct_spread'] == -4.0  # 3.0 - 7.0
        assert result['is_outpacing'] is False

    def test_handles_none_yoy(self):
        """Handles None YoY values."""
        proxy = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=1500000,
            sample_size=10,
            yoy_pct=None,  # No prior year data
            rolling_median_3m=1480000,
            rolling_sample_3m=25,
        )

        target = MetricResult(
            segment='lane_cove_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=2500000,
            sample_size=15,
            yoy_pct=5.0,
            rolling_median_3m=2450000,
            rolling_sample_3m=30,
        )

        result = compute_outpacing_metrics(proxy, target)

        assert result['pct_spread'] is None
        assert result['is_outpacing'] is None

    def test_dollar_spread_computation(self):
        """Computes dollar spread correctly."""
        proxy = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=1000000,  # $1M
            sample_size=10,
            yoy_pct=10.0,  # +$100k
            rolling_median_3m=980000,
            rolling_sample_3m=25,
        )

        target = MetricResult(
            segment='lane_cove_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=2000000,  # $2M
            sample_size=15,
            yoy_pct=5.0,  # +$100k
            rolling_median_3m=1950000,
            rolling_sample_3m=30,
        )

        result = compute_outpacing_metrics(proxy, target)

        # Proxy gained $100k, target gained $100k = $0 spread
        assert result['dollar_spread'] == 0

    def test_dollar_spread_proxy_gaining_faster(self):
        """Dollar spread when proxy gaining more in absolute terms."""
        proxy = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=1500000,  # $1.5M
            sample_size=10,
            yoy_pct=10.0,  # +$150k
            rolling_median_3m=1480000,
            rolling_sample_3m=25,
        )

        target = MetricResult(
            segment='lane_cove_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
            period_type='monthly',
            median_price=2500000,  # $2.5M
            sample_size=15,
            yoy_pct=4.0,  # +$100k
            rolling_median_3m=2450000,
            rolling_sample_3m=30,
        )

        result = compute_outpacing_metrics(proxy, target)

        # Proxy gained $150k, target gained $100k = +$50k spread
        assert result['dollar_spread'] == 50000
