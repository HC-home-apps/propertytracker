# tests/test_zoning.py
"""Tests for NSW Planning Portal zoning lookup."""

import pytest
from unittest.mock import patch, Mock
from tracker.enrich.zoning import (
    get_zoning,
    parse_zoning_from_response,
    build_zoning_url,
)


class TestParseZoning:
    """Test parsing zoning from API response."""

    def test_parses_r2_zoning(self):
        """Extracts R2 zoning code."""
        response = {
            "zoning": {
                "zoneName": "R2 Low Density Residential"
            }
        }
        assert parse_zoning_from_response(response) == "R2"

    def test_parses_r3_zoning(self):
        """Extracts R3 zoning code."""
        response = {
            "zoning": {
                "zoneName": "R3 Medium Density Residential"
            }
        }
        assert parse_zoning_from_response(response) == "R3"

    def test_parses_b1_zoning(self):
        """Extracts B1 zoning code."""
        response = {
            "zoning": {
                "zoneName": "B1 Neighbourhood Centre"
            }
        }
        assert parse_zoning_from_response(response) == "B1"

    def test_returns_none_when_missing(self):
        """Returns None when zoning not in response."""
        assert parse_zoning_from_response({}) is None
        assert parse_zoning_from_response(None) is None


class TestGetZoning:
    """Test full zoning lookup."""

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_zoning_on_success(self, mock_get):
        """Returns zoning when API call succeeds."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "zoning": {"zoneName": "R2 Low Density Residential"}
        }
        mock_get.return_value = mock_response

        zoning = get_zoning("15 Smith St, Revesby NSW 2212")
        assert zoning == "R2"

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        """Returns None when API returns error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        zoning = get_zoning("15 Smith St, Revesby NSW 2212")
        assert zoning is None
