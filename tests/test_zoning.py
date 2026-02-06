# tests/test_zoning.py
"""Tests for NSW Planning Portal zoning lookup."""

import pytest
from unittest.mock import patch, Mock, call
from tracker.enrich.zoning import (
    get_zoning,
    _search_address,
    _get_zoning_from_layers,
)


class TestSearchAddress:
    """Test address search step."""

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_prop_id_on_match(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"address": "5 REIBA CRESCENT REVESBY 2212", "propId": 1373191}
        ]
        mock_get.return_value = mock_response

        prop_id = _search_address("5 Reiba Cres", "Revesby", "2212")
        assert prop_id == 1373191

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_none_on_empty_results(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        assert _search_address("Nonexistent St", "Nowhere", "0000") is None

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        assert _search_address("5 Reiba Cres", "Revesby", "2212") is None


class TestGetZoningFromLayers:
    """Test layer intersect step."""

    @patch('tracker.enrich.zoning.requests.get')
    def test_extracts_r2_zoning(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"layerName": "Height of Buildings Map", "results": []},
            {"layerName": "Land Zoning Map", "results": [
                {"Zone": "R2", "title": "R2: Low Density Residential"}
            ]},
        ]
        mock_get.return_value = mock_response

        assert _get_zoning_from_layers(1373191) == "R2"

    @patch('tracker.enrich.zoning.requests.get')
    def test_returns_none_when_no_zoning_layer(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"layerName": "Height of Buildings Map", "results": []},
        ]
        mock_get.return_value = mock_response

        assert _get_zoning_from_layers(1373191) is None


class TestGetZoning:
    """Test full two-step zoning lookup."""

    @patch('tracker.enrich.zoning._get_zoning_from_layers')
    @patch('tracker.enrich.zoning._search_address')
    def test_returns_zoning_on_success(self, mock_search, mock_layers):
        mock_search.return_value = 1373191
        mock_layers.return_value = "R2"

        zoning = get_zoning(
            "15 Smith St, Revesby NSW 2212",
            street_address="15 Smith St", suburb="Revesby", postcode="2212"
        )
        assert zoning == "R2"
        mock_search.assert_called_once_with("15 Smith St", "Revesby", "2212")
        mock_layers.assert_called_once_with(1373191)

    @patch('tracker.enrich.zoning._search_address')
    def test_returns_none_when_address_not_found(self, mock_search):
        mock_search.return_value = None

        zoning = get_zoning(
            "999 Fake St, Nowhere NSW 0000",
            street_address="999 Fake St", suburb="Nowhere", postcode="0000"
        )
        assert zoning is None

    def test_returns_none_without_structured_address(self):
        zoning = get_zoning("15 Smith St, Revesby NSW 2212")
        assert zoning is None

    @patch('tracker.enrich.zoning._get_zoning_from_layers')
    @patch('tracker.enrich.zoning._search_address')
    def test_returns_none_when_no_zoning_found(self, mock_search, mock_layers):
        mock_search.return_value = 12345
        mock_layers.return_value = None

        zoning = get_zoning(
            "addr", street_address="15 Smith St", suburb="Revesby", postcode="2212"
        )
        assert zoning is None
