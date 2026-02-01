# tests/test_equity.py
"""Tests for equity and affordability gap calculator."""

import pytest
from tracker.compute.equity import (
    compute_nsw_stamp_duty,
    compute_usable_equity,
    compute_ppor_proceeds,
    compute_affordability_gap,
    format_currency,
    EquityScenario,
    AffordabilityResult,
)


class TestComputeNswStampDuty:
    """Test NSW stamp duty calculation."""

    def test_low_value_property(self):
        """Stamp duty for property under $16k."""
        # $10,000 * 1.25% = $125
        assert compute_nsw_stamp_duty(10000) == 125

    def test_property_at_93k(self):
        """Stamp duty at $93k boundary."""
        # $485 + ($93,000 - $35,000) * 1.75% = $485 + $1,015 = $1,500
        assert compute_nsw_stamp_duty(93000) == 1500

    def test_mid_range_property(self):
        """Stamp duty for typical property."""
        # $800,000: $10,530 + ($800,000 - $351,000) * 4.5%
        # = $10,530 + $20,205 = $30,735
        assert compute_nsw_stamp_duty(800000) == 30735

    def test_high_value_property(self):
        """Stamp duty for property over $1.168M."""
        # $1,500,000: $47,295 + ($1,500,000 - $1,168,000) * 5.5%
        # = $47,295 + $18,260 = $65,555
        assert compute_nsw_stamp_duty(1500000) == 65555

    def test_million_dollar_property(self):
        """Stamp duty for $1M property."""
        # $1,000,000: $10,530 + ($1,000,000 - $351,000) * 4.5%
        # = $10,530 + $29,205 = $39,735
        assert compute_nsw_stamp_duty(1000000) == 39735


class TestComputeUsableEquity:
    """Test usable equity calculation."""

    def test_basic_equity(self):
        """Basic equity calculation."""
        valuation, gross, usable = compute_usable_equity(
            market_value=1500000,
            valuation_haircut=1.0,  # No haircut
            current_debt=600000,
            lvr_cap=0.80,
        )

        assert valuation == 1500000
        assert gross == 900000  # 1.5M - 600k
        # Max borrowing = 1.5M * 80% = 1.2M
        # Usable = 1.2M - 600k = 600k
        assert usable == 600000

    def test_with_haircut(self):
        """Equity with valuation haircut."""
        valuation, gross, usable = compute_usable_equity(
            market_value=1500000,
            valuation_haircut=0.90,  # 10% haircut
            current_debt=600000,
            lvr_cap=0.80,
        )

        assert valuation == 1350000  # 1.5M * 90%
        assert gross == 750000  # 1.35M - 600k
        # Max borrowing = 1.35M * 80% = 1.08M
        # Usable = 1.08M - 600k = 480k
        assert usable == 480000

    def test_high_debt_limits_usable(self):
        """High debt limits usable equity."""
        valuation, gross, usable = compute_usable_equity(
            market_value=1000000,
            valuation_haircut=1.0,
            current_debt=750000,  # 75% LVR
            lvr_cap=0.80,
        )

        assert valuation == 1000000
        assert gross == 250000  # 1M - 750k
        # Max borrowing = 1M * 80% = 800k
        # Usable = 800k - 750k = 50k
        assert usable == 50000

    def test_underwater_property(self):
        """Property underwater (debt > value)."""
        valuation, gross, usable = compute_usable_equity(
            market_value=800000,
            valuation_haircut=0.90,
            current_debt=800000,
            lvr_cap=0.80,
        )

        assert valuation == 720000  # 800k * 90%
        assert gross == 0  # Can't be negative
        assert usable == 0  # Can't borrow more


class TestComputePporProceeds:
    """Test PPOR sale proceeds calculation."""

    def test_basic_proceeds(self):
        """Basic proceeds calculation."""
        costs, net = compute_ppor_proceeds(
            sale_price=1000000,
            selling_cost_rate=0.02,
            debt_to_clear=400000,
        )

        assert costs == 20000  # 1M * 2%
        assert net == 580000  # 1M - 20k - 400k

    def test_high_debt(self):
        """High debt reduces proceeds."""
        costs, net = compute_ppor_proceeds(
            sale_price=800000,
            selling_cost_rate=0.02,
            debt_to_clear=700000,
        )

        assert costs == 16000  # 800k * 2%
        assert net == 84000  # 800k - 16k - 700k

    def test_debt_exceeds_proceeds(self):
        """Debt exceeds sale proceeds (negative equity)."""
        costs, net = compute_ppor_proceeds(
            sale_price=500000,
            selling_cost_rate=0.02,
            debt_to_clear=600000,
        )

        assert costs == 10000  # 500k * 2%
        assert net == 0  # Can't be negative


class TestComputeAffordabilityGap:
    """Test full affordability gap calculation."""

    @pytest.fixture
    def sample_config(self):
        """Sample configuration for tests."""
        return {
            'savings': {
                'current_balance': 150000,
                'monthly_contribution': 5000,
            },
            'ppor': {
                'debt': 400000,
                'selling_cost_rate': 0.02,
            },
            'investment_property': {
                'debt': 600000,
                'refinance_lvr_cap': 0.80,
                'valuation_haircut': {
                    'bear': 0.90,
                    'base': 0.95,
                    'bull': 1.00,
                },
            },
            'purchase_costs': {
                'rate': 0.01,
            },
        }

    def test_returns_all_scenarios(self, sample_config):
        """Returns bear, base, and bull scenarios."""
        result = compute_affordability_gap(
            config=sample_config,
            ip_proxy_value=1500000,
            ppor_proxy_value=850000,
            target_price=2000000,
        )

        assert result.bear is not None
        assert result.base is not None
        assert result.bull is not None

    def test_bear_worst_case(self, sample_config):
        """Bear case has worst (highest) gap."""
        result = compute_affordability_gap(
            config=sample_config,
            ip_proxy_value=1500000,
            ppor_proxy_value=850000,
            target_price=2000000,
        )

        assert result.bear.affordability_gap >= result.base.affordability_gap
        assert result.base.affordability_gap >= result.bull.affordability_gap

    def test_gap_range_correct(self, sample_config):
        """Gap range is (worst, best)."""
        result = compute_affordability_gap(
            config=sample_config,
            ip_proxy_value=1500000,
            ppor_proxy_value=850000,
            target_price=2000000,
        )

        worst, best = result.gap_range
        assert worst == result.bear.affordability_gap
        assert best == result.bull.affordability_gap

    def test_affordable_when_gap_negative(self, sample_config):
        """Affordable when base case gap <= 0."""
        # Use low target price to ensure affordable
        result = compute_affordability_gap(
            config=sample_config,
            ip_proxy_value=1500000,
            ppor_proxy_value=850000,
            target_price=500000,  # Very low target
        )

        assert result.base.affordability_gap < 0
        assert result.is_affordable is True

    def test_months_to_close_calculated(self, sample_config):
        """Months to close gap calculated when not affordable."""
        result = compute_affordability_gap(
            config=sample_config,
            ip_proxy_value=1500000,
            ppor_proxy_value=850000,
            target_price=2500000,  # High target = gap
        )

        if result.base.affordability_gap > 0:
            assert result.months_to_close_gap is not None
            # Should be gap / monthly_savings
            expected = result.base.affordability_gap // 5000 + 1
            assert result.months_to_close_gap == expected

    def test_stamp_duty_included(self, sample_config):
        """Stamp duty is included in total purchase cost."""
        result = compute_affordability_gap(
            config=sample_config,
            ip_proxy_value=1500000,
            ppor_proxy_value=850000,
            target_price=2000000,
        )

        base = result.base
        assert base.stamp_duty > 0
        assert base.total_purchase_cost == (
            base.target_price + base.stamp_duty + base.purchase_costs
        )


class TestFormatCurrency:
    """Test currency formatting."""

    def test_positive_amount(self):
        """Format positive amount."""
        assert format_currency(1500000) == "$1,500,000"

    def test_negative_amount(self):
        """Format negative amount."""
        assert format_currency(-50000) == "-$50,000"

    def test_zero(self):
        """Format zero."""
        assert format_currency(0) == "$0"

    def test_small_amount(self):
        """Format small amount."""
        assert format_currency(500) == "$500"
