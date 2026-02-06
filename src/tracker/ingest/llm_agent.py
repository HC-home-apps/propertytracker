# src/tracker/ingest/llm_agent.py
"""LLM agent for extracting property details from listing pages as a fallback."""

import json
import logging
import time
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_PAGE_TEXT_LENGTH = 8000

# Anthropic API configuration
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL_NAME = "claude-haiku-4-5-20251001"


def fetch_page_content(url: str) -> Optional[str]:
    """Fetch a listing page URL and return text content (HTML stripped).

    Uses BeautifulSoup to strip HTML tags, scripts, styles, nav, header, footer.
    Returns text truncated to 8000 chars to control LLM costs.
    Returns None on any error.

    Args:
        url: The listing page URL to fetch

    Returns:
        Cleaned text content (max 8000 chars) or None on error
    """
    try:
        # 1 second delay before request
        time.sleep(1.0)

        # Use a realistic User-Agent header
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # Parse HTML and strip unwanted elements
        soup = BeautifulSoup(response.text, 'html.parser')

        # Remove script, style, nav, header, footer elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer']):
            element.decompose()

        # Get text content
        text = soup.get_text(separator=' ', strip=True)

        # Truncate to MAX_PAGE_TEXT_LENGTH
        if len(text) > MAX_PAGE_TEXT_LENGTH:
            text = text[:MAX_PAGE_TEXT_LENGTH]

        return text

    except Exception as e:
        logger.error(f"Failed to fetch page content from {url}: {e}")
        return None


def build_extraction_prompt(page_text: str, suburb: str) -> str:
    """Build the LLM prompt for extracting property details.

    Args:
        page_text: The cleaned text content of the listing page
        suburb: The suburb context for the property

    Returns:
        The formatted prompt for the LLM
    """
    prompt = f"""Extract property details from this real estate listing page for a property in {suburb}.

Return ONLY a JSON object with the following fields:
- price: integer (sale price in dollars, null if not found)
- bedrooms: integer (number of bedrooms, null if not found)
- bathrooms: integer (number of bathrooms, null if not found)
- car_spaces: integer (number of car spaces/garage, null if not found)
- year_built: integer (year the property was built, null if not found)
- land_area_sqm: integer (land area in square meters, null if not found)
- property_description: string (brief description, max 100 characters, null if not found)

Set fields to null if they are not found in the listing.

Page content:
{page_text}
"""
    return prompt


def call_llm(prompt: str, api_key: str) -> Optional[str]:
    """Call the Anthropic Claude API to process the prompt.

    Args:
        prompt: The prompt to send to the LLM
        api_key: The Anthropic API key

    Returns:
        The response text content, or None on error
    """
    try:
        headers = {
            'x-api-key': api_key,
            'anthropic-version': ANTHROPIC_VERSION,
            'content-type': 'application/json',
        }

        body = {
            'model': MODEL_NAME,
            'max_tokens': 300,
            'messages': [
                {
                    'role': 'user',
                    'content': prompt,
                }
            ],
        }

        response = requests.post(
            ANTHROPIC_API_URL,
            headers=headers,
            json=body,
            timeout=30,
        )
        response.raise_for_status()

        response_data = response.json()

        # Extract the text content from the response
        content = response_data.get('content', [])
        if content and isinstance(content, list) and len(content) > 0:
            return content[0].get('text', '')

        return None

    except Exception as e:
        logger.error(f"LLM API call failed: {e}")
        return None


def extract_listing_details(
    listing_url: str, suburb: str, api_key: Optional[str] = None
) -> Optional[Dict]:
    """Extract property details from a listing URL using LLM fallback.

    Main entry point for the LLM agent. Fetches page content, builds prompt,
    calls LLM, and parses the JSON response.

    Args:
        listing_url: The URL of the property listing page
        suburb: The suburb context for the property
        api_key: The Anthropic API key (optional)

    Returns:
        Dict with extracted property details, or None on failure
    """
    if not api_key:
        logger.debug("No API key provided, skipping LLM extraction")
        return None

    # Fetch page content
    page_text = fetch_page_content(listing_url)
    if not page_text:
        logger.warning(f"Failed to fetch page content for {listing_url}")
        return None

    # Build extraction prompt
    prompt = build_extraction_prompt(page_text, suburb)

    # Call LLM
    llm_response = call_llm(prompt, api_key)
    if not llm_response:
        logger.warning(f"LLM call failed for {listing_url}")
        return None

    # Parse JSON response (handle markdown code fences)
    try:
        # Strip markdown code fences if present
        response_text = llm_response.strip()
        if response_text.startswith('```json'):
            response_text = response_text[7:]  # Remove ```json
        if response_text.startswith('```'):
            response_text = response_text[3:]  # Remove ```
        if response_text.endswith('```'):
            response_text = response_text[:-3]  # Remove trailing ```
        response_text = response_text.strip()

        # Parse JSON
        result = json.loads(response_text)
        logger.info(f"Successfully extracted details from {listing_url}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        logger.debug(f"Raw LLM response: {llm_response}")
        return None
