# tests/test_telegram.py
"""Tests for Telegram notification integration."""

import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from tracker.notify.telegram import (
    TelegramConfig,
    send_message,
    format_metric_line,
    format_outpacing_line,
    format_monthly_report,
    format_alert,
    _compute_verdict,
)
from tracker.compute.metrics import MetricResult
from tracker.compute.equity import EquityScenario, AffordabilityResult


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
        line = format_metric_line("Revesby Houses", sample_metric)

        assert "Revesby Houses" in line
        assert "$1,500,000" in line
        assert "+8.5%" in line
        assert "n=15" in line

    def test_suppressed_metric(self, suppressed_metric):
        """Format suppressed metric."""
        line = format_metric_line("Revesby Houses", suppressed_metric)

        assert "⚠️" in line
        assert "Insufficient" in line

    def test_quarterly_period_note(self, sample_metric):
        """Shows period note for non-monthly."""
        sample_metric.period_type = 'quarterly'
        line = format_metric_line("Test", sample_metric, show_period=True)

        assert "(quarterly)" in line


class TestFormatOutpacingLine:
    """Test outpacing line formatting."""

    def test_positive_outpacing(self):
        """Format positive outpacing."""
        outpacing = {
            'proxy_segment': 'revesby_houses',
            'target_segment': 'lane_cove_houses',
            'pct_spread': 3.5,
            'dollar_spread': 50000,
        }

        line = format_outpacing_line(
            'revesby_houses', 'lane_cove_houses', outpacing
        )

        assert "📈" in line  # Up arrow
        assert "+3.5%" in line
        assert "$50,000" in line

    def test_negative_outpacing(self):
        """Format negative outpacing."""
        outpacing = {
            'proxy_segment': 'revesby_houses',
            'target_segment': 'lane_cove_houses',
            'pct_spread': -2.0,
            'dollar_spread': -30000,
        }

        line = format_outpacing_line(
            'revesby_houses', 'lane_cove_houses', outpacing
        )

        assert "📉" in line  # Down arrow
        assert "-2.0%" in line


class TestFormatMonthlyReport:
    """Test monthly report formatting."""

    def test_contains_all_sections(self, sample_metric, sample_affordability):
        """Report contains all expected sections."""
        metrics = {
            'revesby_houses': sample_metric,
            'wollstonecraft_units': sample_metric,
            'lane_cove_houses': sample_metric,
        }

        outpacing = [{
            'proxy_segment': 'revesby_houses',
            'target_segment': 'lane_cove_houses',
            'pct_spread': 3.0,
            'dollar_spread': 40000,
        }]

        report = format_monthly_report(
            metrics, outpacing, sample_affordability, "January 2024"
        )

        assert "January 2024" in report
        assert "Market Indices" in report
        assert "Your Properties" in report
        assert "Target Markets" in report
        assert "Outpacing Scoreboard" in report
        assert "Affordability Gap" in report
        assert "Verdict" in report

    def test_shows_affordability_status(self, sample_metric, sample_affordability):
        """Shows affordability status."""
        metrics = {'revesby_houses': sample_metric}
        outpacing = []

        report = format_monthly_report(
            metrics, outpacing, sample_affordability, "January 2024"
        )

        # Not affordable case
        assert "months to close" in report


class TestComputeVerdict:
    """Test verdict computation."""

    def test_gap_narrowing(self):
        """Verdict when gap narrowing."""
        outpacing = [{
            'proxy_segment': 'revesby_houses',
            'target_segment': 'lane_cove_houses',
            'pct_spread': 5.0,
            'dollar_spread': 60000,
        }]

        verdict = _compute_verdict(outpacing, MagicMock())
        assert "narrowing" in verdict.lower()

    def test_gap_widening(self):
        """Verdict when gap widening."""
        outpacing = [{
            'proxy_segment': 'revesby_houses',
            'target_segment': 'lane_cove_houses',
            'pct_spread': -3.0,
            'dollar_spread': -40000,
        }]

        verdict = _compute_verdict(outpacing, MagicMock())
        assert "widening" in verdict.lower()


class TestFormatAlert:
    """Test alert formatting."""

    def test_info_alert(self):
        """Format info alert."""
        alert = format_alert("Test Alert", "Test message", severity='info')

        assert "ℹ️" in alert
        assert "Test Alert" in alert
        assert "Test message" in alert

    def test_warning_alert(self):
        """Format warning alert."""
        alert = format_alert("Warning", "Be careful", severity='warning')

        assert "⚠️" in alert

    def test_error_alert(self):
        """Format error alert."""
        alert = format_alert("Error", "Something broke", severity='error')

        assert "🚨" in alert
