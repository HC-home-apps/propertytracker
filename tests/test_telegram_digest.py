# tests/test_telegram_digest.py
import pytest
from unittest.mock import patch, MagicMock
from tracker.notify.telegram import (
    format_review_digest,
    build_digest_keyboard,
    send_review_digest,
    TelegramConfig,
)


class TestFormatReviewDigest:
    def test_formats_single_sale(self):
        sales = [{
            'sale_id': 'ABC123',
            'address': '15 Alliance Ave',
            'price': 1420000,
            'area_sqm': 556.0,
            'zoning_label': 'R2',
            'year_built_label': 'Built 1965',
            'listing_url': 'https://www.domain.com.au/15-alliance-ave-abc123',
        }]
        msg = format_review_digest('Revesby Houses', sales)
        assert '📋' in msg
        assert 'Revesby Houses' in msg
        assert '1 to review' in msg
        assert '<a href="https://www.domain.com.au/15-alliance-ave-abc123">15 Alliance Ave</a>' in msg
        assert '$1,420,000' in msg
        assert 'R2' in msg
        assert 'Built 1965' in msg

    def test_formats_multiple_sales(self):
        sales = [
            {
                'sale_id': 'ABC123',
                'address': '15 Alliance Ave',
                'price': 1420000,
                'area_sqm': 556.0,
                'zoning_label': 'R2',
                'year_built_label': 'Built 1965',
                'listing_url': 'https://www.domain.com.au/abc123',
            },
            {
                'sale_id': 'DEF456',
                'address': '20 Smith St',
                'price': 1380000,
                'area_sqm': 580.0,
                'zoning_label': 'R2',
                'year_built_label': 'Built 1972',
                'listing_url': 'https://www.domain.com.au/def456',
            },
        ]
        msg = format_review_digest('Revesby Houses', sales)
        assert '2 to review' in msg
        assert '1.' in msg
        assert '2.' in msg

    def test_shows_year_unknown(self):
        sales = [{
            'sale_id': 'X1',
            'address': '8 Jones Ave',
            'price': 1350000,
            'area_sqm': 520.0,
            'zoning_label': 'Zoning unverified',
            'year_built_label': 'Year unknown',
            'listing_url': 'https://www.google.com/search?q=8+Jones+Ave+Revesby+sold',
        }]
        msg = format_review_digest('Revesby Houses', sales)
        assert 'Year unknown' in msg
        assert 'Zoning unverified' in msg

    def test_uses_google_fallback_link(self):
        sales = [{
            'sale_id': 'X2',
            'address': '5 Test Rd',
            'price': 1000000,
            'area_sqm': None,
            'zoning_label': 'R2',
            'year_built_label': 'Built 2000',
            'listing_url': None,
        }]
        msg = format_review_digest('Revesby Houses', sales)
        assert 'google.com/search' in msg


class TestBuildDigestKeyboard:
    def test_one_row_per_sale_plus_bulk(self):
        sale_ids = [('ABC123', 'seg1'), ('DEF456', 'seg1')]
        keyboard = build_digest_keyboard(sale_ids, 'seg1')
        # 2 sale rows + 1 bulk row = 3 rows
        assert len(keyboard['inline_keyboard']) == 3
        # Each sale row has 2 buttons (yes/no)
        assert len(keyboard['inline_keyboard'][0]) == 2
        # Bulk row has 2 buttons
        assert len(keyboard['inline_keyboard'][2]) == 2

    def test_callback_data_format(self):
        sale_ids = [('ABC123', 'seg1')]
        keyboard = build_digest_keyboard(sale_ids, 'seg1')
        yes_btn = keyboard['inline_keyboard'][0][0]
        no_btn = keyboard['inline_keyboard'][0][1]
        assert yes_btn['callback_data'] == 'review:seg1:ABC123:yes'
        assert no_btn['callback_data'] == 'review:seg1:ABC123:no'

    def test_bulk_callback_data(self):
        sale_ids = [('ABC123', 'seg1'), ('DEF456', 'seg1')]
        keyboard = build_digest_keyboard(sale_ids, 'seg1')
        bulk_row = keyboard['inline_keyboard'][2]
        assert 'all' in bulk_row[0]['callback_data']
        assert 'all' in bulk_row[1]['callback_data']

    def test_max_5_sales_per_keyboard(self):
        sale_ids = [(f'SALE{i}', 'seg1') for i in range(7)]
        keyboard = build_digest_keyboard(sale_ids[:5], 'seg1')
        # 5 sale rows + 1 bulk row = 6 rows
        assert len(keyboard['inline_keyboard']) == 6


class TestSendReviewDigest:
    @patch('tracker.notify.telegram.requests.post')
    def test_sends_message_with_keyboard(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        mock_post.return_value = mock_response

        config = TelegramConfig(bot_token='token', chat_id='123')
        sales = [{
            'sale_id': 'ABC123',
            'address': '15 Alliance Ave',
            'price': 1420000,
            'area_sqm': 556.0,
            'zoning_label': 'R2',
            'year_built_label': 'Built 1965',
            'listing_url': 'https://www.domain.com.au/abc123',
        }]
        result = send_review_digest(config, 'Revesby Houses', sales, 'revesby_houses')
        assert result is True
        mock_post.assert_called_once()
