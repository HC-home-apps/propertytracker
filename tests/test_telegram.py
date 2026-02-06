# tests/test_telegram.py
"""Tests for Telegram notification integration."""

import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from tracker.notify.telegram import (
    TelegramConfig,
    send_message,
    format_metric_line,
    format_monthly_report,
    format_alert,
    format_gap_tracker_section,
    format_affordability_section,
    _compute_verdict,
)
from tracker.compute.metrics import MetricResult
from tracker.compute.equity import EquityScenario, AffordabilityResult
from tracker.compute.gap_tracker import GapTrackerResult


@pytest.fixture
def sample_metric():
    """Sample MetricResult for tests."""
    return MetricResult(
        segment='revesby_houses',
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 31),
        period_type='monthly',
        median_price=1500000,
        sample_size=15,
        yoy_pct=8.5,
        rolling_median_3m=1480000,
        rolling_sample_3m=40,
        display_name='Revesby Houses (IP)',
        filter_description='500-600sqm land',
    )


@pytest.fixture
def suppressed_metric():
    """Suppressed MetricResult for tests."""
    return MetricResult(
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
        suppression_reason='Insufficient data: 2 < 8',
        display_name='Revesby Houses (IP)',
    )


@pytest.fixture
def sample_gap_tracker():
    """Sample GapTrackerResult for tests."""
    return GapTrackerResult(
        proxy_total_change=120000,
        proxy_breakdown={
            'revesby_houses': {
                'display_name': 'Revesby Houses (IP)',
                'median': 1500000,
                'yoy_pct': 8.0,
                'change': 120000,
                'sample_size': 15,
                'is_suppressed': False,
                'filter_description': '500-600sqm land',
            },
        },
        target_segment='lane_cove_houses',
        target_display_name='Lane Cove Houses',
        target_median=2500000,
        target_yoy=4.0,
        target_change=100000,
        net_position=20000,
        is_catching_up=True,
        can_compute=True,
        computation_notes=[],
    )


@pytest.fixture
def sample_affordability():
    """Sample AffordabilityResult for tests."""
    base_scenario = EquityScenario(
        scenario='base',
        ip_proxy_value=1500000,
        ip_valuation=1425000,
        ip_debt=600000,
        ip_gross_equity=825000,
        ip_usable_equity=540000,
        ppor_proxy_value=850000,
        ppor_selling_costs=16150,
        ppor_debt=400000,
        ppor_net_proceeds=391100,
        savings_balance=150000,
        monthly_savings=5000,
        total_cash=1081100,
        target_price=2000000,
        stamp_duty=84735,
        purchase_costs=20000,
        total_purchase_cost=2104735,
        affordability_gap=1023635,
    )

    bear_scenario = EquityScenario(
        scenario='bear',
        ip_proxy_value=1500000,
        ip_valuation=1350000,
        ip_debt=600000,
        ip_gross_equity=750000,
        ip_usable_equity=480000,
        ppor_proxy_value=850000,
        ppor_selling_costs=15300,
        ppor_debt=400000,
        ppor_net_proceeds=349700,
        savings_balance=150000,
        monthly_savings=5000,
        total_cash=979700,
        target_price=2000000,
        stamp_duty=84735,
        purchase_costs=20000,
        total_purchase_cost=2104735,
        affordability_gap=1125035,
    )

    bull_scenario = EquityScenario(
        scenario='bull',
        ip_proxy_value=1500000,
        ip_valuation=1500000,
        ip_debt=600000,
        ip_gross_equity=900000,
        ip_usable_equity=600000,
        ppor_proxy_value=850000,
        ppor_selling_costs=17000,
        ppor_debt=400000,
        ppor_net_proceeds=433000,
        savings_balance=150000,
        monthly_savings=5000,
        total_cash=1183000,
        target_price=2000000,
        stamp_duty=84735,
        purchase_costs=20000,
        total_purchase_cost=2104735,
        affordability_gap=921735,
    )

    return AffordabilityResult(
        bear=bear_scenario,
        base=base_scenario,
        bull=bull_scenario,
        gap_range=(1125035, 921735),
        is_affordable=False,
        months_to_close_gap=205,
    )


class TestTelegramConfig:
    """Test Telegram configuration."""

    def test_from_env_with_values(self):
        """Load config from environment."""
        with patch.dict('os.environ', {
            'TELEGRAM_BOT_TOKEN': 'test_token',
            'TELEGRAM_CHAT_ID': '12345',
        }):
            config = TelegramConfig.from_env()
            assert config.bot_token == 'test_token'
            assert config.chat_id == '12345'

    def test_from_env_missing_token(self):
        """Raises when token missing."""
        with patch.dict('os.environ', {
            'TELEGRAM_CHAT_ID': '12345',
        }, clear=True):
            with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
                TelegramConfig.from_env()


class TestSendMessage:
    """Test message sending."""

    @patch('tracker.notify.telegram.requests.post')
    def test_successful_send(self, mock_post):
        """Successfully send message."""
        mock_post.return_value.status_code = 200

        config = TelegramConfig(bot_token='token', chat_id='123')
        result = send_message(config, "Test message")

        assert result is True
        mock_post.assert_called_once()

    @patch('tracker.notify.telegram.requests.post')
    def test_failed_send(self, mock_post):
        """Handle send failure."""
        import requests as req
        mock_post.side_effect = req.RequestException("Network error")

        config = TelegramConfig(bot_token='token', chat_id='123')
        result = send_message(config, "Test message")

        assert result is False


class TestFormatMetricLine:
    """Test metric line formatting."""

    def test_normal_metric(self, sample_metric):
        """Format normal metric."""
        line = format_metric_line(sample_metric)

        assert "Revesby Houses (IP)" in line
        assert "$1,500,000" in line
        assert "+8.5%" in line
        assert "n=15" in line

    def test_suppressed_metric(self, suppressed_metric):
        """Format suppressed metric."""
        line = format_metric_line(suppressed_metric)

        assert "Insufficient" in line

    def test_includes_filter_description(self, sample_metric):
        """Shows filter description when available."""
        line = format_metric_line(sample_metric, include_filter=True)

        assert "500-600sqm land" in line

    def test_quarterly_period_note(self, sample_metric):
        """Shows period note for non-monthly."""
        sample_metric = MetricResult(
            segment='revesby_houses',
            period_start=date(2024, 1, 1),
            period_end=date(2024, 3, 31),
            period_type='quarterly',
            median_price=1500000,
            sample_size=15,
            yoy_pct=8.5,
            rolling_median_3m=1480000,
            rolling_sample_3m=40,
            display_name='Revesby Houses',
        )
        line = format_metric_line(sample_metric)

        assert "(quarterly)" in line


class TestFormatGapTrackerSection:
    """Test gap tracker section formatting."""

    def test_catching_up(self, sample_gap_tracker):
        """Format when catching up."""
        lines = format_gap_tracker_section(sample_gap_tracker)
        text = '\n'.join(lines)

        assert "Your assets this year" in text
        assert "+$120,000" in text
        assert "Lane Cove Houses" in text
        assert "Catching up" in text

    def test_falling_behind(self):
        """Format when falling behind."""
        gap_tracker = GapTrackerResult(
            proxy_total_change=50000,
            proxy_breakdown={},
            target_segment='lane_cove_houses',
            target_display_name='Lane Cove Houses',
            target_median=2500000,
            target_yoy=4.0,
            target_change=100000,
            net_position=-50000,
            is_catching_up=False,
            can_compute=True,
            computation_notes=[],
        )
        lines = format_gap_tracker_section(gap_tracker)
        text = '\n'.join(lines)

        assert "Falling behind" in text

    def test_cannot_compute(self):
        """Format when cannot compute."""
        gap_tracker = GapTrackerResult(
            proxy_total_change=None,
            proxy_breakdown={},
            target_segment='lane_cove_houses',
            target_display_name='Lane Cove Houses',
            target_median=None,
            target_yoy=None,
            target_change=None,
            net_position=None,
            is_catching_up=None,
            can_compute=False,
            computation_notes=['Missing data'],
        )
        lines = format_gap_tracker_section(gap_tracker)
        text = '\n'.join(lines)

        assert "Cannot compute" in text


class TestFormatAffordabilitySection:
    """Test affordability section formatting."""

    def test_shows_target_breakdown(self, sample_affordability):
        """Shows target cost breakdown."""
        lines = format_affordability_section(sample_affordability, 'Lane Cove Houses')
        text = '\n'.join(lines)

        assert "Lane Cove Houses" in text
        assert "Stamp duty" in text
        assert "Total needed" in text

    def test_shows_cash_breakdown(self, sample_affordability):
        """Shows cash sources."""
        lines = format_affordability_section(sample_affordability, 'Target')
        text = '\n'.join(lines)

        assert "Savings" in text
        assert "Total available" in text

    def test_shows_gap(self, sample_affordability):
        """Shows gap calculation."""
        lines = format_affordability_section(sample_affordability, 'Target')
        text = '\n'.join(lines)

        assert "Gap:" in text


class TestFormatMonthlyReport:
    """Test monthly report formatting."""

    def test_contains_all_sections(self, sample_metric, sample_gap_tracker, sample_affordability):
        """Report contains all expected sections."""
        metrics = {
            'revesby_houses': sample_metric,
            'wollstonecraft_units': sample_metric,
            'lane_cove_houses': sample_metric,
        }

        report = format_monthly_report(
            metrics, sample_gap_tracker, sample_affordability, "January 2024"
        )

        assert "January 2024" in report
        assert "Your Properties" in report
        assert "Target Markets" in report
        assert "Gap Tracker" in report
        assert "Affordability Gap" in report
        assert "Verdict" in report


class TestComputeVerdict:
    """Test verdict computation."""

    def test_catching_up(self, sample_gap_tracker, sample_affordability):
        """Verdict when catching up."""
        verdict = _compute_verdict(sample_gap_tracker, sample_affordability)
        # Should mention progress
        assert "progress" in verdict.lower() or "gaining" in verdict.lower()

    def test_falling_behind(self, sample_affordability):
        """Verdict when falling behind."""
        gap_tracker = GapTrackerResult(
            proxy_total_change=50000,
            proxy_breakdown={},
            target_segment='lane_cove_houses',
            target_display_name='Lane Cove Houses',
            target_median=2500000,
            target_yoy=4.0,
            target_change=100000,
            net_position=-60000,
            is_catching_up=False,
            can_compute=True,
            computation_notes=[],
        )
        verdict = _compute_verdict(gap_tracker, sample_affordability)
        assert "widen" in verdict.lower() or "headwind" in verdict.lower()


class TestFormatAlert:
    """Test alert formatting."""

    def test_info_alert(self):
        """Format info alert."""
        alert = format_alert("Test Alert", "Test message", severity='info')

        assert "Test Alert" in alert
        assert "Test message" in alert

    def test_warning_alert(self):
        """Format warning alert."""
        alert = format_alert("Warning", "Be careful", severity='warning')
        assert "Warning" in alert

    def test_error_alert(self):
        """Format error alert."""
        alert = format_alert("Error", "Something broke", severity='error')
        assert "Error" in alert


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
