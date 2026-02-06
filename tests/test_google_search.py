# tests/test_google_search.py
"""Tests for Google search scraper module."""

import pytest
from unittest.mock import patch, MagicMock

from tracker.ingest.google_search import (
    build_search_query,
    parse_search_results_html,
    extract_listing_data,
    _deduplicate_results,
    fetch_sold_listings_google,
    REAL_ESTATE_DOMAINS,
)


# ---------------------------------------------------------------------------
# Helpers: realistic Google search result HTML snippets
# ---------------------------------------------------------------------------

def _make_google_result_html(url, title, snippet):
    """Build a single Google search result div."""
    return f'''
    <div class="g">
        <a href="{url}">
            <h3>{title}</h3>
        </a>
        <div class="VwiC3b">{snippet}</div>
    </div>
    '''


def _wrap_results_page(*result_htmls):
    """Wrap result divs in a minimal Google-like page."""
    body = '\n'.join(result_htmls)
    return f'<html><body>{body}</body></html>'


# ---------------------------------------------------------------------------
# TestBuildSearchQuery
# ---------------------------------------------------------------------------

class TestBuildSearchQuery:
    def test_house_query(self):
        query = build_search_query('Revesby', 'house')
        assert query == 'sold Revesby house'

    def test_unit_query_with_beds_and_baths(self):
        query = build_search_query('Wollstonecraft', 'unit', bedrooms=2, bathrooms=1)
        assert query == 'sold Wollstonecraft 2 bed 1 bath apartment'

    def test_unit_query_without_filters(self):
        query = build_search_query('Lane Cove', 'unit')
        assert query == 'sold Lane Cove apartment'

    def test_house_query_with_beds_only(self):
        query = build_search_query('Chatswood', 'house', bedrooms=3)
        assert query == 'sold Chatswood 3 bed house'


# ---------------------------------------------------------------------------
# TestParseSearchResultsHtml
# ---------------------------------------------------------------------------

class TestParseSearchResultsHtml:
    def test_extracts_domain_listing(self):
        html = _wrap_results_page(
            _make_google_result_html(
                'https://www.domain.com.au/5-10-shirley-road-wollstonecraft-nsw-2065-12345',
                '5/10 Shirley Road, Wollstonecraft NSW 2065',
                'Sold $1,200,000 on 15 Jan 2026. 2 bed 1 bath 1 car.',
            )
        )
        results = parse_search_results_html(html)
        assert len(results) == 1
        assert results[0]['source_site'] == 'domain.com.au'
        assert results[0]['title'] == '5/10 Shirley Road, Wollstonecraft NSW 2065'
        assert '$1,200,000' in results[0]['snippet']

    def test_extracts_realestate_listing(self):
        html = _wrap_results_page(
            _make_google_result_html(
                'https://www.realestate.com.au/sold/property-unit-nsw-wollstonecraft-12345',
                '5/10 Shirley Road, Wollstonecraft NSW 2065',
                'Sold $1,200,000. 2 bed 1 bath.',
            )
        )
        results = parse_search_results_html(html)
        assert len(results) == 1
        assert results[0]['source_site'] == 'realestate.com.au'

    def test_ignores_non_real_estate_results(self):
        html = _wrap_results_page(
            _make_google_result_html(
                'https://www.wikipedia.org/wiki/Wollstonecraft',
                'Wollstonecraft - Wikipedia',
                'Some unrelated content about the suburb.',
            ),
            _make_google_result_html(
                'https://www.domain.com.au/sold-listing-12345',
                '10 Smith St, Wollstonecraft',
                'Sold $900,000',
            ),
        )
        results = parse_search_results_html(html)
        assert len(results) == 1
        assert results[0]['source_site'] == 'domain.com.au'

    def test_handles_allhomes_listing(self):
        html = _wrap_results_page(
            _make_google_result_html(
                'https://www.allhomes.com.au/sold/property-12345',
                '15 Alliance Ave, Revesby NSW 2212',
                'Sold $1,500,000. 3 bed 2 bath 2 car. 556sqm',
            )
        )
        results = parse_search_results_html(html)
        assert len(results) == 1
        assert results[0]['source_site'] == 'allhomes.com.au'

    def test_handles_empty_html(self):
        results = parse_search_results_html('<html><body></body></html>')
        assert results == []

    def test_handles_missing_snippet(self):
        html = _wrap_results_page(
            '<div class="g"><a href="https://www.domain.com.au/listing"><h3>Title</h3></a></div>'
        )
        results = parse_search_results_html(html)
        assert len(results) == 1
        assert results[0]['snippet'] == ''

    def test_uses_data_sncf_fallback(self):
        html = _wrap_results_page(
            '''<div class="g">
                <a href="https://www.domain.com.au/listing"><h3>Title</h3></a>
                <div data-sncf="1">Fallback snippet text</div>
            </div>'''
        )
        results = parse_search_results_html(html)
        assert len(results) == 1
        assert results[0]['snippet'] == 'Fallback snippet text'


# ---------------------------------------------------------------------------
# TestExtractListingData
# ---------------------------------------------------------------------------

class TestExtractListingData:
    def test_price_extraction_full_format(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Sold $1,420,000 on 20 Jan 2026. 3 bed 2 bath 2 car.',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_price'] == 1420000
        assert listing['price_withheld'] is False

    def test_price_extraction_millions_format(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Sold for $1.42m. 3 bed 2 bath.',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_price'] == 1420000

    def test_price_extraction_millions_uppercase(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Sold $2.1M',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_price'] == 2100000

    def test_price_withheld(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Price withheld. 3 bed 2 bath.',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_price'] is None
        assert listing['price_withheld'] is True

    def test_price_undisclosed(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Sold - price undisclosed. 3 bed.',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_price'] is None
        assert listing['price_withheld'] is True

    def test_contact_agent(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Contact agent for price.',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_price'] is None
        assert listing['price_withheld'] is True

    def test_address_parsing_unit_format(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '5/10 Shirley Rd, Wollstonecraft NSW 2065',
            'snippet': 'Sold $1,200,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Wollstonecraft', '2065')
        assert listing['unit_number'] == '5'
        assert listing['house_number'] == '10'
        assert listing['street_name'] == 'Shirley Rd'

    def test_address_parsing_house_format(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '15 Alliance Ave, Revesby NSW 2212',
            'snippet': 'Sold $1,500,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['unit_number'] is None
        assert listing['house_number'] == '15'
        assert listing['street_name'] == 'Alliance Ave'

    def test_beds_baths_car_extraction(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': '3 bed 2 bath 2 car. 556sqm. Sold $1,400,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['bedrooms'] == 3
        assert listing['bathrooms'] == 2
        assert listing['car_spaces'] == 2

    def test_area_sqm_extraction(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': '556sqm block. Sold $1,400,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['area_sqm'] == 556.0

    def test_area_m2_extraction(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': '400m\u00b2 land. Sold $1,200,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['area_sqm'] == 400.0

    def test_sold_date_parsing_month_name(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Sold on 15 Jan 2026 for $1,200,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_date'] == '2026-01-15'

    def test_sold_date_parsing_slash_format(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Sold 15/01/2026 $1,200,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_date'] == '2026-01-15'

    def test_normalised_address_populated(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': '5/10 Shirley Road, Wollstonecraft NSW 2065',
            'snippet': 'Sold $1,200,000',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Wollstonecraft', '2065')
        assert listing['address_normalised'] is not None
        assert '|' in listing['address_normalised']
        assert 'wollstonecraft' in listing['address_normalised']

    def test_missing_fields_handled_gracefully(self):
        result = {
            'url': 'https://www.domain.com.au/listing',
            'title': 'Some property listing',
            'snippet': 'No useful data here.',
            'source_site': 'domain.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['sold_price'] is None
        assert listing['bedrooms'] is None
        assert listing['sold_date'] is None
        assert listing['price_withheld'] is False

    def test_listing_url_and_source_preserved(self):
        result = {
            'url': 'https://www.realestate.com.au/sold/property-12345',
            'title': '10 Smith St, Revesby NSW 2212',
            'snippet': 'Sold $1,000,000',
            'source_site': 'realestate.com.au',
        }
        listing = extract_listing_data(result, 'Revesby', '2212')
        assert listing['listing_url'] == 'https://www.realestate.com.au/sold/property-12345'
        assert listing['source_site'] == 'realestate.com.au'


# ---------------------------------------------------------------------------
# TestDeduplicateResults
# ---------------------------------------------------------------------------

class TestDeduplicateResults:
    def test_keeps_single_listings(self):
        listings = [
            {
                'address_normalised': '|10|smith st|revesby|2212',
                'source_site': 'domain.com.au',
                'sold_price': 1400000,
                'bedrooms': 3,
            },
        ]
        result = _deduplicate_results(listings)
        assert len(result) == 1

    def test_prefers_domain_over_realestate(self):
        listings = [
            {
                'address_normalised': '|10|smith st|revesby|2212',
                'source_site': 'realestate.com.au',
                'sold_price': 1400000,
                'listing_url': 'https://realestate.com.au/listing',
                'bedrooms': None,
            },
            {
                'address_normalised': '|10|smith st|revesby|2212',
                'source_site': 'domain.com.au',
                'sold_price': 1400000,
                'listing_url': 'https://domain.com.au/listing',
                'bedrooms': 3,
            },
        ]
        result = _deduplicate_results(listings)
        assert len(result) == 1
        assert result[0]['source_site'] == 'domain.com.au'
        assert result[0]['listing_url'] == 'https://domain.com.au/listing'

    def test_merges_missing_fields_from_duplicate(self):
        listings = [
            {
                'address_normalised': '|10|smith st|revesby|2212',
                'source_site': 'domain.com.au',
                'sold_price': 1400000,
                'bedrooms': None,
                'bathrooms': None,
            },
            {
                'address_normalised': '|10|smith st|revesby|2212',
                'source_site': 'realestate.com.au',
                'sold_price': 1400000,
                'bedrooms': 3,
                'bathrooms': 2,
            },
        ]
        result = _deduplicate_results(listings)
        assert len(result) == 1
        assert result[0]['source_site'] == 'domain.com.au'
        assert result[0]['bedrooms'] == 3
        assert result[0]['bathrooms'] == 2

    def test_different_addresses_not_merged(self):
        listings = [
            {
                'address_normalised': '|10|smith st|revesby|2212',
                'source_site': 'domain.com.au',
                'sold_price': 1400000,
            },
            {
                'address_normalised': '|15|alliance ave|revesby|2212',
                'source_site': 'domain.com.au',
                'sold_price': 1500000,
            },
        ]
        result = _deduplicate_results(listings)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestFetchSoldListingsGoogle
# ---------------------------------------------------------------------------

class TestFetchSoldListingsGoogle:
    @patch('tracker.ingest.google_search.time.sleep')
    @patch('tracker.ingest.google_search.requests.get')
    def test_returns_parsed_results(self, mock_get, mock_sleep):
        html = _wrap_results_page(
            _make_google_result_html(
                'https://www.domain.com.au/sold-listing-12345',
                '10 Smith St, Revesby NSW 2212',
                'Sold $1,400,000. 3 bed 2 bath 2 car.',
            )
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_get.return_value = mock_response

        results = fetch_sold_listings_google('Revesby', 'house', '2212')
        assert len(results) == 1
        assert results[0]['sold_price'] == 1400000
        assert results[0]['source_site'] == 'domain.com.au'
        mock_sleep.assert_called_once()

    @patch('tracker.ingest.google_search.time.sleep')
    @patch('tracker.ingest.google_search.requests.get')
    def test_returns_empty_on_http_429(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_get.return_value = mock_response

        results = fetch_sold_listings_google('Revesby', 'house', '2212')
        assert results == []

    @patch('tracker.ingest.google_search.time.sleep')
    @patch('tracker.ingest.google_search.requests.get')
    def test_returns_empty_on_exception(self, mock_get, mock_sleep):
        mock_get.side_effect = Exception("Network error")

        results = fetch_sold_listings_google('Revesby', 'house', '2212')
        assert results == []

    @patch('tracker.ingest.google_search.time.sleep')
    @patch('tracker.ingest.google_search.requests.get')
    def test_returns_empty_on_request_exception(self, mock_get, mock_sleep):
        import requests as req
        mock_get.side_effect = req.ConnectionError("Connection refused")

        results = fetch_sold_listings_google('Revesby', 'house', '2212')
        assert results == []

    @patch('tracker.ingest.google_search.time.sleep')
    @patch('tracker.ingest.google_search.requests.get')
    def test_passes_query_params(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><body></body></html>'
        mock_get.return_value = mock_response

        fetch_sold_listings_google('Wollstonecraft', 'unit', '2065', bedrooms=2, bathrooms=1)

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get('params') or call_kwargs[1].get('params')
        assert params['q'] == 'sold Wollstonecraft 2 bed 1 bath apartment'
        assert params['gl'] == 'au'
        assert params['hl'] == 'en'

    @patch('tracker.ingest.google_search.time.sleep')
    @patch('tracker.ingest.google_search.requests.get')
    def test_deduplicates_results(self, mock_get, mock_sleep):
        html = _wrap_results_page(
            _make_google_result_html(
                'https://www.domain.com.au/10-smith-st-revesby-12345',
                '10 Smith St, Revesby NSW 2212',
                'Sold $1,400,000. 3 bed.',
            ),
            _make_google_result_html(
                'https://www.realestate.com.au/sold/10-smith-st-revesby',
                '10 Smith St, Revesby NSW 2212',
                'Sold $1,400,000. 3 bed 2 bath 2 car.',
            ),
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_get.return_value = mock_response

        results = fetch_sold_listings_google('Revesby', 'house', '2212')
        assert len(results) == 1
        assert results[0]['source_site'] == 'domain.com.au'
        # Merged bathrooms from realestate listing
        assert results[0]['bathrooms'] == 2
