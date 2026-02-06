# tests/test_cli_review.py
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from tracker.cli import cli


class TestReviewButtonsDigest:
    @patch('tracker.cli.TelegramConfig')
    @patch('tracker.cli.send_review_digest')
    @patch('tracker.cli.SEGMENTS')
    @patch('tracker.cli.Database')
    @patch('tracker.cli.load_config')
    @patch('tracker.cli.init_segments')
    def test_sends_digest_per_segment(self, mock_init_seg, mock_config, mock_db_cls,
                                      mock_segments, mock_send, mock_tg_config):
        """review-buttons should send one digest per segment, not individual messages."""
        mock_config.return_value = {'segments': {}}

        # Mock database
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        # Mock segment
        mock_segment = MagicMock()
        mock_segment.display_name = 'Revesby Houses'
        mock_segment.suburbs = frozenset(['revesby'])
        mock_segment.property_type = 'house'
        mock_segments.get.return_value = mock_segment

        # Return 2 pending sales for one segment
        mock_db.query.return_value = [
            {'sale_id': 'A1', 'address': '15 Smith St', 'suburb': 'Revesby',
             'price': 850000, 'area_sqm': 450, 'zoning': 'R2',
             'year_built': 1965, 'listing_url': 'https://domain.com.au/a1'},
            {'sale_id': 'A2', 'address': '20 Jones Ave', 'suburb': 'Revesby',
             'price': 920000, 'area_sqm': 500, 'zoning': 'R2',
             'year_built': None, 'listing_url': None},
        ]
        mock_send.return_value = True

        # Mock TelegramConfig.from_env
        mock_tg_config.from_env.return_value = MagicMock()

        runner = CliRunner()
        result = runner.invoke(cli, ['review-buttons', '--segment', 'revesby_houses'])

        # Should call send_review_digest once (not send_review_with_buttons twice)
        mock_send.assert_called_once()
