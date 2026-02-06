# tests/test_cli_ingest_google.py
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from tracker.cli import cli

class TestIngestGoogleCommand:
    @patch('tracker.cli.fetch_sold_listings_google')
    @patch('tracker.cli.SEGMENTS')
    @patch('tracker.cli.Database')
    @patch('tracker.cli.load_config')
    @patch('tracker.cli.init_segments')
    def test_ingests_google_results(self, mock_init_seg, mock_config, mock_db_cls,
                                     mock_segments, mock_fetch):
        mock_config.return_value = {'database': {'path': 'data/test.db'}}

        # Mock segment
        mock_segment = MagicMock()
        mock_segment.display_name = 'Revesby Houses'
        mock_segment.suburbs = frozenset(['revesby'])
        mock_segment.property_type = 'house'
        mock_segment.bedrooms = None
        mock_segment.bathrooms = None
        mock_segment.require_manual_review = True
        mock_segments.items.return_value = [('revesby_houses', mock_segment)]

        # Mock database
        mock_db = MagicMock()
        mock_db_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_db_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value = [{'postcode': '2212'}]
        mock_db.upsert_provisional_sales.return_value = 1

        # Mock Google fetch with minimal required fields
        mock_fetch.return_value = [{
            'address_normalised': 'revesby|nsw|2212',
            'unit_number': None,
            'house_number': '15',
            'street_name': 'Smith St',
            'suburb': 'Revesby',
            'postcode': '2212',
            'sold_price': 850000,
            'sold_date': '2026-01-15',
            'bedrooms': 3,
            'bathrooms': 2,
            'car_spaces': 2,
            'listing_url': 'https://domain.com.au/test',
            'source_site': 'domain.com.au',
            'price_withheld': False,
        }]

        runner = CliRunner()
        result = runner.invoke(cli, ['ingest-google'])
        assert result.exit_code == 0

        # Verify fetch was called with correct params
        mock_fetch.assert_called_once_with(
            suburb='revesby',
            property_type='house',
            postcode='2212',
            bedrooms=None,
            bathrooms=None,
        )

        # Verify upsert was called
        assert mock_db.upsert_provisional_sales.called
