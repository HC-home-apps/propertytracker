# tests/test_domain.py
"""Tests for Domain API client."""

import pytest
from unittest.mock import patch, Mock
from tracker.enrich.domain import (
    get_year_built,
    build_domain_search_url,
    parse_year_built_from_response,
)


class TestBuildSearchUrl:
    """Test URL building for Domain API."""

    def test_builds_url_with_address(self):
        """Builds correct search URL."""
        url = build_domain_search_url("15 Smith St", "Revesby", "2212")
        assert "domain.com.au" in url or "api.domain.com.au" in url
        assert "Smith" in url or "smith" in url.lower()


class TestParseYearBuilt:
    """Test parsing year built from API response."""

    def test_parses_year_from_response(self):
        """Extracts year built from API response."""
        response = {
            "propertyDetails": {
                "yearBuilt": 1965
            }
        }
        assert parse_year_built_from_response(response) == 1965

    def test_returns_none_when_missing(self):
        """Returns None when year not in response."""
        response = {"propertyDetails": {}}
        assert parse_year_built_from_response(response) is None

    def test_handles_empty_response(self):
        """Handles empty response."""
        assert parse_year_built_from_response({}) is None
        assert parse_year_built_from_response(None) is None


class TestGetYearBuilt:
    """Test full year built lookup."""

    @patch('tracker.enrich.domain.requests.get')
    def test_returns_year_on_success(self, mock_get):
        """Returns year built when API call succeeds."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "propertyDetails": {"yearBuilt": 1972}
        }
        mock_get.return_value = mock_response

        year = get_year_built("15 Smith St", "Revesby", "2212", api_key="test_key")
        assert year == 1972

    @patch('tracker.enrich.domain.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        """Returns None when API returns error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        year = get_year_built("15 Smith St", "Revesby", "2212", api_key="test_key")
        assert year is None

    def test_returns_none_without_api_key(self):
        """Returns None when no API key configured."""
        year = get_year_built("15 Smith St", "Revesby", "2212", api_key=None)
        assert year is None
