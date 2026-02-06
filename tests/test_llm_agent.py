# tests/test_llm_agent.py
"""Tests for LLM agent fallback module."""

import json
from unittest.mock import Mock, patch

import pytest

from tracker.ingest.llm_agent import (
    MAX_PAGE_TEXT_LENGTH,
    build_extraction_prompt,
    call_llm,
    extract_listing_details,
    fetch_page_content,
)


class TestBuildExtractionPrompt:
    """Tests for build_extraction_prompt function."""

    def test_includes_suburb_context(self):
        """Test that the prompt includes the suburb context."""
        page_text = "Sample listing text"
        suburb = "Lane Cove"

        prompt = build_extraction_prompt(page_text, suburb)

        assert "Lane Cove" in prompt
        assert suburb in prompt

    def test_includes_page_text(self):
        """Test that the prompt includes the page text."""
        page_text = "3 bed 2 bath house with pool"
        suburb = "Chatswood"

        prompt = build_extraction_prompt(page_text, suburb)

        assert page_text in prompt
        assert "3 bed 2 bath house with pool" in prompt

    def test_asks_for_json(self):
        """Test that the prompt asks for JSON output."""
        page_text = "Sample listing"
        suburb = "Revesby"

        prompt = build_extraction_prompt(page_text, suburb)

        assert "JSON" in prompt
        # Check for expected fields
        assert "price" in prompt
        assert "bedrooms" in prompt
        assert "bathrooms" in prompt
        assert "car_spaces" in prompt
        assert "year_built" in prompt
        assert "land_area_sqm" in prompt
        assert "property_description" in prompt


class TestFetchPageContent:
    """Tests for fetch_page_content function."""

    @patch('tracker.ingest.llm_agent.requests.get')
    @patch('tracker.ingest.llm_agent.time.sleep')
    def test_fetches_and_strips_html(self, mock_sleep, mock_get):
        """Test that the function fetches and strips HTML properly."""
        # Mock HTML response
        html_content = """
        <html>
            <head>
                <script>console.log('test');</script>
                <style>.test { color: red; }</style>
            </head>
            <body>
                <header>Header content</header>
                <nav>Navigation</nav>
                <main>
                    <h1>Beautiful 3 Bedroom House</h1>
                    <p>Price: $1,200,000</p>
                    <p>Features: 3 bed, 2 bath, 2 car</p>
                </main>
                <footer>Footer content</footer>
            </body>
        </html>
        """

        mock_response = Mock()
        mock_response.text = html_content
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = fetch_page_content('https://example.com/listing')

        # Verify sleep was called
        mock_sleep.assert_called_once_with(1.0)

        # Verify request was made with proper headers
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert 'User-Agent' in call_args[1]['headers']

        # Verify result contains main content but not scripts/styles/nav/header/footer
        assert result is not None
        assert 'Beautiful 3 Bedroom House' in result
        assert 'Price: $1,200,000' in result
        assert '3 bed, 2 bath, 2 car' in result
        assert 'console.log' not in result
        assert 'color: red' not in result
        assert 'Header content' not in result
        assert 'Navigation' not in result
        assert 'Footer content' not in result

    @patch('tracker.ingest.llm_agent.requests.get')
    @patch('tracker.ingest.llm_agent.time.sleep')
    def test_truncates_long_content(self, mock_sleep, mock_get):
        """Test that content is truncated to MAX_PAGE_TEXT_LENGTH."""
        # Create HTML with very long text
        long_text = "A" * (MAX_PAGE_TEXT_LENGTH + 1000)
        html_content = f"<html><body><p>{long_text}</p></body></html>"

        mock_response = Mock()
        mock_response.text = html_content
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = fetch_page_content('https://example.com/listing')

        assert result is not None
        assert len(result) == MAX_PAGE_TEXT_LENGTH

    @patch('tracker.ingest.llm_agent.requests.get')
    @patch('tracker.ingest.llm_agent.time.sleep')
    def test_returns_none_on_error(self, mock_sleep, mock_get):
        """Test that the function returns None on any error."""
        # Simulate request error
        mock_get.side_effect = Exception("Network error")

        result = fetch_page_content('https://example.com/listing')

        assert result is None

    @patch('tracker.ingest.llm_agent.requests.get')
    @patch('tracker.ingest.llm_agent.time.sleep')
    def test_returns_none_on_http_error(self, mock_sleep, mock_get):
        """Test that the function returns None on HTTP error."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        mock_get.return_value = mock_response

        result = fetch_page_content('https://example.com/listing')

        assert result is None


class TestCallLlm:
    """Tests for call_llm function."""

    @patch('tracker.ingest.llm_agent.requests.post')
    def test_calls_api_with_correct_parameters(self, mock_post):
        """Test that the LLM API is called with correct parameters."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'content': [{'text': '{"price": 1000000}'}]
        }
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        prompt = "Extract property details"
        api_key = "test-api-key"

        result = call_llm(prompt, api_key)

        # Verify the POST call
        mock_post.assert_called_once()
        call_args = mock_post.call_args

        # Check URL
        assert call_args[0][0] == "https://api.anthropic.com/v1/messages"

        # Check headers
        headers = call_args[1]['headers']
        assert headers['x-api-key'] == api_key
        assert headers['anthropic-version'] == "2023-06-01"
        assert headers['content-type'] == "application/json"

        # Check body
        body = call_args[1]['json']
        assert body['model'] == "claude-haiku-4-5-20251001"
        assert body['max_tokens'] == 300
        assert len(body['messages']) == 1
        assert body['messages'][0]['role'] == 'user'
        assert body['messages'][0]['content'] == prompt

        # Check timeout
        assert call_args[1]['timeout'] == 30

        # Verify result
        assert result == '{"price": 1000000}'

    @patch('tracker.ingest.llm_agent.requests.post')
    def test_returns_none_on_error(self, mock_post):
        """Test that the function returns None on API error."""
        mock_post.side_effect = Exception("API error")

        result = call_llm("test prompt", "test-api-key")

        assert result is None


class TestExtractListingDetails:
    """Tests for extract_listing_details function."""

    @patch('tracker.ingest.llm_agent.call_llm')
    @patch('tracker.ingest.llm_agent.fetch_page_content')
    def test_returns_structured_data(self, mock_fetch, mock_call_llm):
        """Test that the function returns structured data when successful."""
        # Mock successful page fetch
        mock_fetch.return_value = "3 bed 2 bath house for sale at $1,200,000"

        # Mock successful LLM call with JSON response
        llm_response = json.dumps({
            "price": 1200000,
            "bedrooms": 3,
            "bathrooms": 2,
            "car_spaces": 2,
            "year_built": 2010,
            "land_area_sqm": 500,
            "property_description": "Modern family home"
        })
        mock_call_llm.return_value = llm_response

        result = extract_listing_details(
            listing_url='https://example.com/listing',
            suburb='Lane Cove',
            api_key='test-api-key'
        )

        # Verify both mocks were called
        mock_fetch.assert_called_once_with('https://example.com/listing')
        mock_call_llm.assert_called_once()

        # Verify result structure
        assert result is not None
        assert result['price'] == 1200000
        assert result['bedrooms'] == 3
        assert result['bathrooms'] == 2
        assert result['car_spaces'] == 2
        assert result['year_built'] == 2010
        assert result['land_area_sqm'] == 500
        assert result['property_description'] == "Modern family home"

    @patch('tracker.ingest.llm_agent.call_llm')
    @patch('tracker.ingest.llm_agent.fetch_page_content')
    def test_handles_markdown_code_fences(self, mock_fetch, mock_call_llm):
        """Test that the function handles markdown code fences in LLM response."""
        mock_fetch.return_value = "Sample listing content"

        # Mock LLM response with markdown code fences
        llm_response = '```json\n{"price": 1000000, "bedrooms": 3}\n```'
        mock_call_llm.return_value = llm_response

        result = extract_listing_details(
            listing_url='https://example.com/listing',
            suburb='Chatswood',
            api_key='test-api-key'
        )

        assert result is not None
        assert result['price'] == 1000000
        assert result['bedrooms'] == 3

    @patch('tracker.ingest.llm_agent.call_llm')
    @patch('tracker.ingest.llm_agent.fetch_page_content')
    def test_handles_code_fence_without_json_label(self, mock_fetch, mock_call_llm):
        """Test that the function handles code fences without json label."""
        mock_fetch.return_value = "Sample listing content"

        # Mock LLM response with code fences but no json label
        llm_response = '```\n{"price": 800000}\n```'
        mock_call_llm.return_value = llm_response

        result = extract_listing_details(
            listing_url='https://example.com/listing',
            suburb='Revesby',
            api_key='test-api-key'
        )

        assert result is not None
        assert result['price'] == 800000

    @patch('tracker.ingest.llm_agent.fetch_page_content')
    def test_returns_none_when_page_fails(self, mock_fetch):
        """Test that the function returns None when page fetch fails."""
        mock_fetch.return_value = None

        result = extract_listing_details(
            listing_url='https://example.com/listing',
            suburb='Lane Cove',
            api_key='test-api-key'
        )

        assert result is None

    @patch('tracker.ingest.llm_agent.call_llm')
    @patch('tracker.ingest.llm_agent.fetch_page_content')
    def test_returns_none_when_llm_fails(self, mock_fetch, mock_call_llm):
        """Test that the function returns None when LLM call fails."""
        mock_fetch.return_value = "Sample content"
        mock_call_llm.return_value = None

        result = extract_listing_details(
            listing_url='https://example.com/listing',
            suburb='Chatswood',
            api_key='test-api-key'
        )

        assert result is None

    def test_returns_none_without_api_key(self):
        """Test that the function returns None without an API key."""
        result = extract_listing_details(
            listing_url='https://example.com/listing',
            suburb='Lane Cove',
            api_key=None
        )

        assert result is None

    @patch('tracker.ingest.llm_agent.call_llm')
    @patch('tracker.ingest.llm_agent.fetch_page_content')
    def test_returns_none_on_invalid_json(self, mock_fetch, mock_call_llm):
        """Test that the function returns None when LLM returns invalid JSON."""
        mock_fetch.return_value = "Sample content"
        mock_call_llm.return_value = "This is not valid JSON"

        result = extract_listing_details(
            listing_url='https://example.com/listing',
            suburb='Revesby',
            api_key='test-api-key'
        )

        assert result is None
