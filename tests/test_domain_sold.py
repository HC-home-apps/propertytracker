# tests/test_domain_sold.py
import pytest
from unittest.mock import patch, MagicMock
from tracker.ingest.domain_sold import (
    fetch_sold_listings,
    parse_sold_listing,
    build_sold_search_params,
)


class TestBuildSoldSearchParams:
    def test_builds_params_for_suburb(self):
        params = build_sold_search_params(suburb='Wollstonecraft', property_type='unit', postcode='2065')
        assert params['suburb'] == 'Wollstonecraft'
        assert params['propertyTypes'] == ['unit']
        assert params['postcode'] == '2065'

    def test_maps_house_type(self):
        params = build_sold_search_params(suburb='Revesby', property_type='house', postcode='2212')
        assert params['propertyTypes'] == ['house']


class TestParseSoldListing:
    def test_parses_unit_listing(self):
        raw = {
            'id': 12345,
            'unitNumber': '9',
            'streetNumber': '27-29',
            'streetName': 'Morton',
            'streetType': 'Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'propertyType': 'ApartmentUnitFlat',
            'price': 1200000,
            'soldDate': '2026-02-03',
        }
        result = parse_sold_listing(raw)
        assert result['id'] == 'domain-12345'
        assert result['source'] == 'domain'
        assert result['unit_number'] == '9'
        assert result['house_number'] == '27-29'
        assert result['street_name'] == 'Morton Street'
        assert result['suburb'] == 'Wollstonecraft'
        assert result['property_type'] == 'unit'
        assert result['sold_price'] == 1200000
        assert result['sold_date'] == '2026-02-03'

    def test_parses_house_listing(self):
        raw = {
            'id': 67890,
            'unitNumber': None,
            'streetNumber': '5',
            'streetName': 'Smith',
            'streetType': 'Street',
            'suburb': 'Revesby',
            'postcode': '2212',
            'propertyType': 'House',
            'price': 1500000,
            'soldDate': '2026-01-20',
        }
        result = parse_sold_listing(raw)
        assert result['id'] == 'domain-67890'
        assert result['property_type'] == 'house'
        assert result['unit_number'] is None

    def test_skips_listing_without_price(self):
        raw = {
            'id': 99999,
            'unitNumber': None,
            'streetNumber': '1',
            'streetName': 'Fake',
            'streetType': 'Street',
            'suburb': 'Revesby',
            'postcode': '2212',
            'propertyType': 'House',
            'price': None,
            'soldDate': '2026-01-20',
        }
        result = parse_sold_listing(raw)
        assert result is None


class TestFetchSoldListings:
    @patch('tracker.ingest.domain_sold.requests.get')
    def test_returns_parsed_listings(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                'id': 11111,
                'unitNumber': '3',
                'streetNumber': '10',
                'streetName': 'Test',
                'streetType': 'Road',
                'suburb': 'Wollstonecraft',
                'postcode': '2065',
                'propertyType': 'ApartmentUnitFlat',
                'price': 800000,
                'soldDate': '2026-01-15',
            }
        ]
        mock_get.return_value = mock_response

        results = fetch_sold_listings(
            suburb='Wollstonecraft', property_type='unit', postcode='2065', api_key='test-key',
        )
        assert len(results) == 1
        assert results[0]['id'] == 'domain-11111'

    @patch('tracker.ingest.domain_sold.requests.get')
    def test_returns_empty_on_api_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        results = fetch_sold_listings(
            suburb='Wollstonecraft', property_type='unit', postcode='2065', api_key='test-key',
        )
        assert results == []

    def test_returns_empty_without_api_key(self):
        results = fetch_sold_listings(
            suburb='Wollstonecraft', property_type='unit', postcode='2065', api_key=None,
        )
        assert results == []
