# tests/test_cli_review.py
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from tracker.cli import cli, _is_provisional_id


class TestIsProvisionalId:
    """Test the _is_provisional_id helper function."""

    def test_google_id_is_provisional(self):
        assert _is_provisional_id('google-12345') is True

    def test_domain_id_is_provisional(self):
        assert _is_provisional_id('domain-67890') is True

    def test_vg_dealing_number_is_not_provisional(self):
        assert _is_provisional_id('AU999999') is False

    def test_numeric_id_is_not_provisional(self):
        assert _is_provisional_id('12345') is False

    def test_empty_string_is_not_provisional(self):
        assert _is_provisional_id('') is False


class TestReviewButtonsDigest:
    @patch('tracker.cli.TelegramConfig')
    @patch('tracker.cli.send_review_digest')
    @patch('tracker.cli.SEGMENTS')
    @patch('tracker.cli.Database')
    @patch('tracker.cli.load_config')
    @patch('tracker.cli.init_segments')
    def test_sends_digest_with_vg_sales(self, mock_init_seg, mock_config, mock_db_cls,
                                        mock_segments, mock_send, mock_tg_config):
        """review-buttons should send VG sales for review."""
        mock_config.return_value = {'segments': {}}

        # Mock database - returns VG rows from first query, empty from second
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        # Mock segment
        mock_segment = MagicMock()
        mock_segment.display_name = 'Revesby Houses'
        mock_segment.suburbs = frozenset(['revesby'])
        mock_segment.property_type = 'house'
        mock_segment.bedrooms = None
        mock_segment.bathrooms = None
        mock_segment.car_spaces = None
        mock_segments.get.return_value = mock_segment

        # First call returns VG rows, second call returns provisional rows (empty)
        mock_db.query.side_effect = [
            [
                {'sale_id': 'A1', 'address': '15 Smith St', 'suburb': 'Revesby',
                 'price': 850000, 'area_sqm': 450, 'zoning': 'R2',
                 'year_built': 1965, 'listing_url': 'https://domain.com.au/a1',
                 'bedrooms': None, 'bathrooms': None, 'car_spaces': None,
                 'source_site': None, 'sold_date': None, 'source_type': 'vg'},
            ],
            [],  # No provisional sales
        ]
        mock_send.return_value = True

        # Mock TelegramConfig.from_env
        mock_tg_config.from_env.return_value = MagicMock()

        runner = CliRunner()
        result = runner.invoke(cli, ['review-buttons', '--segment', 'revesby_houses'])

        mock_send.assert_called_once()

    @patch('tracker.cli.SEGMENTS')
    @patch('tracker.cli.Database')
    @patch('tracker.cli.load_config')
    @patch('tracker.cli.init_segments')
    def test_dry_run_shows_both_sources(self, mock_init_seg, mock_config, mock_db_cls,
                                        mock_segments):
        """Dry run should label provisional and VG sales differently."""
        mock_config.return_value = {'segments': {}}

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        mock_segment = MagicMock()
        mock_segment.display_name = 'Revesby Houses'
        mock_segment.suburbs = frozenset(['revesby'])
        mock_segment.property_type = 'house'
        mock_segment.bedrooms = None
        mock_segment.bathrooms = None
        mock_segment.car_spaces = None
        mock_segments.get.return_value = mock_segment

        # First call: VG rows, second call: provisional rows
        mock_db.query.side_effect = [
            [
                {'sale_id': 'A1', 'address': '15 Smith St', 'suburb': 'Revesby',
                 'price': 850000, 'area_sqm': 450, 'zoning': 'R2',
                 'year_built': 1965, 'listing_url': None,
                 'bedrooms': None, 'bathrooms': None, 'car_spaces': None,
                 'source_site': None, 'sold_date': None, 'source_type': 'vg'},
            ],
            [
                {'sale_id': 'google-123', 'address': '20 Jones Ave', 'suburb': 'Revesby',
                 'price': 920000, 'area_sqm': None, 'zoning': None,
                 'year_built': None, 'listing_url': 'https://domain.com.au/xyz',
                 'bedrooms': 3, 'bathrooms': 2, 'car_spaces': 1,
                 'source_site': 'domain.com.au', 'sold_date': '2026-01-28',
                 'source_type': 'provisional'},
            ],
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ['review-buttons', '--segment', 'revesby_houses', '--dry-run'])

        assert '[PROV]' in result.output
        assert '[VG]' in result.output
