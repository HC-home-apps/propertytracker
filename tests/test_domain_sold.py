# tests/test_domain_sold.py
import pytest
from unittest.mock import patch, MagicMock
from tracker.ingest.domain_sold import (
    fetch_sold_listings,
    parse_sold_listing,
    build_sold_search_body,
)


class TestBuildSoldSearchBody:
    def test_builds_body_for_unit(self):
        body = build_sold_search_body(suburb='Wollstonecraft', property_type='unit', postcode='2065')
        assert body['listingType'] == 'Sold'
        assert body['propertyTypes'] == ['ApartmentUnitFlat']
        assert body['locations'][0]['suburb'] == 'Wollstonecraft'
        assert body['locations'][0]['postcode'] == '2065'

    def test_builds_body_for_house(self):
        body = build_sold_search_body(suburb='Revesby', property_type='house', postcode='2212')
        assert body['listingType'] == 'Sold'
        assert 'House' in body['propertyTypes']
        assert 'Townhouse' in body['propertyTypes']


class TestParseSoldListing:
    def test_parses_flat_listing(self):
        """Flat format (legacy / salesResults)."""
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

    def test_parses_nested_search_listing(self):
        """Nested format from POST /v1/listings/residential/_search."""
        raw = {
            'type': 'PropertyListing',
            'listing': {
                'id': 54321,
                'listingSlug': '9-27-29-morton-street-wollstonecraft-nsw-2065-54321',
                'propertyDetails': {
                    'unitNumber': '9',
                    'streetNumber': '27-29',
                    'street': 'Morton Street',
                    'suburb': 'Wollstonecraft',
                    'postcode': '2065',
                    'propertyType': 'ApartmentUnitFlat',
                    'bedrooms': 2,
                    'bathrooms': 1,
                    'carspaces': 1,
                },
                'saleDetails': {
                    'soldDate': '2026-02-03T00:00:00',
                    'soldPrice': 1200000,
                },
            },
        }
        result = parse_sold_listing(raw)
        assert result['id'] == 'domain-54321'
        assert result['unit_number'] == '9'
        assert result['house_number'] == '27-29'
        assert result['street_name'] == 'Morton Street'
        assert result['suburb'] == 'Wollstonecraft'
        assert result['property_type'] == 'unit'
        assert result['sold_price'] == 1200000
        assert result['sold_date'] == '2026-02-03'  # datetime truncated
        assert result['bedrooms'] == 2
        assert result['bathrooms'] == 1
        assert result['car_spaces'] == 1
        assert result['listing_url'] == 'https://www.domain.com.au/9-27-29-morton-street-wollstonecraft-nsw-2065-54321'

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

    def test_skips_listing_without_id(self):
        raw = {
            'unitNumber': None,
            'streetNumber': '1',
            'streetName': 'Fake',
            'streetType': 'Street',
            'suburb': 'Revesby',
            'postcode': '2212',
            'propertyType': 'House',
            'price': 1000000,
            'soldDate': '2026-01-20',
        }
        result = parse_sold_listing(raw)
        assert result is None


class TestFetchSoldListings:
    @patch('tracker.ingest.domain_sold.requests.post')
    def test_returns_parsed_listings(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                'type': 'PropertyListing',
                'listing': {
                    'id': 11111,
                    'propertyDetails': {
                        'unitNumber': '3',
                        'streetNumber': '10',
                        'street': 'Test Road',
                        'suburb': 'Wollstonecraft',
                        'postcode': '2065',
                        'propertyType': 'ApartmentUnitFlat',
                        'bedrooms': 2,
                        'bathrooms': 1,
                    },
                    'saleDetails': {
                        'soldPrice': 800000,
                        'soldDate': '2026-01-15T00:00:00',
                    },
                },
            }
        ]
        mock_post.return_value = mock_response

        results = fetch_sold_listings(
            suburb='Wollstonecraft', property_type='unit', postcode='2065', api_key='test-key',
        )
        assert len(results) == 1
        assert results[0]['id'] == 'domain-11111'
        assert results[0]['sold_price'] == 800000

    @patch('tracker.ingest.domain_sold.requests.post')
    def test_returns_empty_on_api_error(self, mock_post):
        mock_post.side_effect = Exception("Network error")
        results = fetch_sold_listings(
            suburb='Wollstonecraft', property_type='unit', postcode='2065', api_key='test-key',
        )
        assert results == []

    def test_returns_empty_without_api_key(self):
        results = fetch_sold_listings(
            suburb='Wollstonecraft', property_type='unit', postcode='2065', api_key=None,
        )
        assert results == []


class TestFetchSoldListingsFiltering:
    @patch('tracker.ingest.domain_sold.requests.post')
    def test_filters_by_property_type(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                'type': 'PropertyListing',
                'listing': {
                    'id': 22222,
                    'propertyDetails': {
                        'unitNumber': None,
                        'streetNumber': '15',
                        'street': 'Alliance Avenue',
                        'suburb': 'Revesby',
                        'postcode': '2212',
                        'propertyType': 'House',
                    },
                    'saleDetails': {
                        'soldPrice': 1420000,
                        'soldDate': '2026-02-03T00:00:00',
                    },
                },
            },
            {
                'type': 'PropertyListing',
                'listing': {
                    'id': 33333,
                    'propertyDetails': {
                        'unitNumber': '3',
                        'streetNumber': '10',
                        'street': 'Test Road',
                        'suburb': 'Revesby',
                        'postcode': '2212',
                        'propertyType': 'ApartmentUnitFlat',
                    },
                    'saleDetails': {
                        'soldPrice': 800000,
                        'soldDate': '2026-01-15T00:00:00',
                    },
                },
            },
        ]
        mock_post.return_value = mock_response

        results = fetch_sold_listings(
            suburb='Revesby', property_type='house', postcode='2212', api_key='test-key',
        )
        # Should only return the house, not the unit
        assert len(results) == 1
        assert results[0]['id'] == 'domain-22222'
