# tests/test_review_telegram.py
"""Tests for Telegram review message formatting and parsing."""

import pytest
import tempfile
import os
from tracker.db import Database
from tracker.review.telegram import (
    format_review_message,
    parse_review_reply,
    format_domain_url,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(db_path=path)
    db.init_schema()
    yield db
    db.close()
    os.unlink(path)


class TestFormatDomainUrl:
    """Test Domain URL generation."""

    def test_formats_url_correctly(self):
        """Generates correct Domain search URL."""
        url = format_domain_url("15 Smith St", "Revesby")
        assert "domain.com.au" in url
        assert "smith" in url.lower() or "revesby" in url.lower()


class TestFormatReviewMessage:
    """Test review message formatting."""

    def test_formats_single_sale(self):
        """Formats single sale for review."""
        sales = [{
            'sale_id': 'DN123',
            'address': '15 Smith St, Revesby',
            'price': 1420000,
            'area_sqm': 556,
            'zoning': 'R2',
            'year_built': 1965,
        }]

        message = format_review_message(sales)

        assert '15 Smith St' in message
        assert '$1,420,000' in message
        assert '556' in message
        assert 'R2' in message
        assert '1965' in message
        assert '1.' in message  # Numbered

    def test_formats_multiple_sales(self):
        """Formats multiple sales with numbers."""
        sales = [
            {'sale_id': 'DN1', 'address': '15 Smith St', 'price': 1400000, 'area_sqm': 550, 'zoning': 'R2', 'year_built': 1965},
            {'sale_id': 'DN2', 'address': '20 Jones Ave', 'price': 1500000, 'area_sqm': 580, 'zoning': 'R2', 'year_built': 1970},
        ]

        message = format_review_message(sales)

        assert '1.' in message
        assert '2.' in message
        assert '15 Smith St' in message
        assert '20 Jones Ave' in message

    def test_includes_reply_instructions(self):
        """Includes instructions for replying."""
        sales = [{'sale_id': 'DN1', 'address': '15 Smith St', 'price': 1400000, 'area_sqm': 550, 'zoning': 'R2', 'year_built': None}]

        message = format_review_message(sales)

        assert 'Reply' in message or 'reply' in message


class TestParseReviewReply:
    """Test reply parsing."""

    def test_parses_shorthand_emojis(self):
        """Parses shorthand emoji replies."""
        result = parse_review_reply("✅✅❌", sale_count=3)
        assert result == ['comparable', 'comparable', 'not_comparable']

    def test_parses_numbered_replies(self):
        """Parses numbered replies."""
        result = parse_review_reply("1✅ 2❌ 3✅", sale_count=3)
        assert result == ['comparable', 'not_comparable', 'comparable']

    def test_parses_all_approve(self):
        """Parses 'all' shortcut."""
        result = parse_review_reply("all✅", sale_count=3)
        assert result == ['comparable', 'comparable', 'comparable']

    def test_parses_skip(self):
        """Parses skip command."""
        result = parse_review_reply("skip", sale_count=3)
        assert result == ['pending', 'pending', 'pending']

    def test_handles_mixed_case(self):
        """Handles mixed case input."""
        result = parse_review_reply("ALL✅", sale_count=2)
        assert result == ['comparable', 'comparable']

    def test_handles_spaces(self):
        """Handles spaces between emojis."""
        result = parse_review_reply("✅ ✅ ❌", sale_count=3)
        assert result == ['comparable', 'comparable', 'not_comparable']

    def test_returns_none_for_invalid(self):
        """Returns None for invalid/incomplete replies."""
        result = parse_review_reply("✅", sale_count=3)  # Only 1 of 3
        assert result is None

        result = parse_review_reply("hello", sale_count=3)
        assert result is None
