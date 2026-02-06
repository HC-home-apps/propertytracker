# src/tracker/ingest/google_search.py
"""Scrape DuckDuckGo search results for recently sold properties.

Uses DuckDuckGo HTML version (html.duckduckgo.com) which works without
JavaScript rendering. Searches site:domain.com.au for sold listings.
"""

import logging
import random
import re
import time
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from tracker.ingest.normalise import normalise_address

logger = logging.getLogger(__name__)

REAL_ESTATE_DOMAINS = ['domain.com.au', 'realestate.com.au', 'allhomes.com.au']

DOMAIN_PRIORITY = {
    'domain.com.au': 1,
    'realestate.com.au': 2,
    'allhomes.com.au': 3,
}

DDG_SEARCH_URL = 'https://html.duckduckgo.com/html/'

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
]


def build_search_query(
    suburb: str,
    property_type: str,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[int] = None,
) -> str:
    """Build a search query string for sold properties on domain.com.au.

    Args:
        suburb: Suburb name (e.g. 'Wollstonecraft')
        property_type: 'house' or 'unit'
        bedrooms: Optional bedroom count filter
        bathrooms: Optional bathroom count filter

    Returns:
        Search query string
    """
    type_label = 'apartment' if property_type == 'unit' else 'house'

    parts = ['site:domain.com.au', 'sold', suburb]
    if bedrooms is not None:
        parts.append(f'{bedrooms} bed')
    if bathrooms is not None:
        parts.append(f'{bathrooms} bath')
    parts.append(type_label)

    return ' '.join(parts)


def parse_search_results_html(html: str) -> List[dict]:
    """Parse DuckDuckGo HTML search results and extract real estate listings.

    Filters to results from known real estate domains only.

    Args:
        html: Raw HTML from DuckDuckGo search results page

    Returns:
        List of dicts with keys: url, title, snippet, source_site
    """
    soup = BeautifulSoup(html, 'html.parser')
    results = []

    for div in soup.select('div.result'):
        # DDG HTML structure: h2.result__title > a.result__a
        link = div.select_one('a.result__a')
        if not link:
            continue

        url = link.get('href', '')
        source_site = _match_real_estate_domain(url)
        if not source_site:
            continue

        title = link.get_text(strip=True)

        # Snippet: a.result__snippet
        snippet_el = div.select_one('a.result__snippet')
        snippet = snippet_el.get_text(strip=True) if snippet_el else ''

        results.append({
            'url': url,
            'title': title,
            'snippet': snippet,
            'source_site': source_site,
        })

    return results


def _match_real_estate_domain(url: str) -> Optional[str]:
    """Check if a URL belongs to a known real estate domain.

    Returns the matched domain string or None.
    """
    for domain in REAL_ESTATE_DOMAINS:
        if domain in url:
            return domain
    return None


def extract_listing_data(result: dict, suburb: str, postcode: str) -> dict:
    """Extract structured listing data from a search result.

    Args:
        result: Dict with url, title, snippet, source_site
        suburb: Expected suburb name
        postcode: Expected postcode

    Returns:
        Dict with parsed listing fields
    """
    title = result.get('title', '')
    snippet = result.get('snippet', '')
    combined_text = f"{title} {snippet}"

    # Parse address from title
    unit_number, house_number, street_name = _parse_address_from_title(title, suburb)

    # Parse price
    sold_price, price_withheld = _parse_price(combined_text)

    # Parse bedrooms/bathrooms/car
    bedrooms = _parse_int_field(combined_text, r'(\d+)\s*bed')
    bathrooms = _parse_int_field(combined_text, r'(\d+)\s*bath')
    car_spaces = _parse_int_field(combined_text, r'(\d+)\s*car')

    # Parse area
    area_sqm = _parse_area(combined_text)

    # Parse sold date from combined text (title has "Sold DD Mon", snippet has "on DD Mon")
    sold_date = _parse_sold_date(combined_text)

    # Normalise address
    address_normalised = normalise_address(
        unit_number=unit_number,
        house_number=house_number or '',
        street_name=street_name or '',
        suburb=suburb,
        postcode=postcode,
    )

    return {
        'unit_number': unit_number,
        'house_number': house_number,
        'street_name': street_name,
        'suburb': suburb,
        'postcode': postcode,
        'sold_price': sold_price,
        'sold_date': sold_date,
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'car_spaces': car_spaces,
        'area_sqm': area_sqm,
        'listing_url': result.get('url', ''),
        'source_site': result.get('source_site', ''),
        'address_normalised': address_normalised,
        'price_withheld': price_withheld,
    }


def _parse_address_from_title(title: str, suburb: str) -> tuple:
    """Parse address components from a search result title.

    Handles formats like:
    - "5/10 Shirley Rd, Wollstonecraft NSW 2065 - Sold ..."  (unit)
    - "15 Alliance Ave, Revesby NSW 2212 - Sold ..."          (house)

    Returns:
        (unit_number, house_number, street_name) tuple
    """
    cleaned = title
    if suburb:
        pattern = re.compile(
            r'[,\s]*\b' + re.escape(suburb) + r'\b.*$',
            re.IGNORECASE,
        )
        cleaned = pattern.sub('', cleaned).strip()

    # Strip common suffixes like " - Domain.com.au", " | realestate.com.au"
    cleaned = re.sub(r'\s*[-|].*$', '', cleaned).strip()

    # Strip trailing state/postcode if still present (e.g., "NSW 2065")
    cleaned = re.sub(r'\s+(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s*\d{0,4}\s*$', '', cleaned, flags=re.IGNORECASE).strip()

    # Try to match unit format: "5/10 Street Name" or "5/10-12 Street Name"
    unit_match = re.match(r'^(\d+[a-zA-Z]?)\s*/\s*(\d+(?:-\d+)?[a-zA-Z]?)\s+(.+)$', cleaned)
    if unit_match:
        return unit_match.group(1), unit_match.group(2), unit_match.group(3).strip()

    # Try to match house format: "15 Street Name" or "10-12 Street Name"
    house_match = re.match(r'^(\d+(?:-\d+)?[a-zA-Z]?)\s+(.+)$', cleaned)
    if house_match:
        return None, house_match.group(1), house_match.group(2).strip()

    return None, None, None


def _parse_price(text: str) -> tuple:
    """Parse sold price from snippet text.

    Handles: $1,420,000 | $1.42m | $1.42M | price withheld | undisclosed | contact agent

    Returns:
        (price_int_or_none, price_withheld_bool)
    """
    lower = text.lower()

    # Check for withheld/undisclosed
    if any(term in lower for term in ['price withheld', 'undisclosed', 'contact agent']):
        return None, True

    # Try $X.XXm / $X.XXM format (e.g., "$1.42m", "$2.1M")
    m_match = re.search(r'\$(\d+(?:\.\d+)?)\s*[mM]', text)
    if m_match:
        price = float(m_match.group(1)) * 1_000_000
        return int(price), False

    # Try $X,XXX,XXX format
    full_match = re.search(r'\$([\d,]+)', text)
    if full_match:
        price_str = full_match.group(1).replace(',', '')
        try:
            return int(price_str), False
        except ValueError:
            pass

    return None, False


def _parse_int_field(text: str, pattern: str) -> Optional[int]:
    """Extract an integer from text using a regex pattern."""
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, IndexError):
            pass
    return None


def _parse_area(text: str) -> Optional[float]:
    """Parse area in square metres from text.

    Handles: 556sqm, 556m2, 556 m2, 556 sqm, 556m\u00b2
    """
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:sqm|m2|m\u00b2)', text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def _parse_sold_date(text: str) -> Optional[str]:
    """Parse sold date from text.

    Handles formats like:
    - "Sold on 15 Jan 2026"
    - "Sold 15/01/2026"
    - "sold for $1,100,000 on 06 Dec 2025"
    - "- Sold 06 Dec 2025"
    """
    month_map = {
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
        'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
    }

    # "Sold on DD Mon YYYY" or "Sold DD Mon YYYY" (direct after Sold)
    match = re.search(
        r'[Ss]old\s+(?:on\s+)?(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})',
        text,
        re.IGNORECASE,
    )
    if match:
        day = match.group(1).zfill(2)
        month = month_map.get(match.group(2).lower(), '01')
        year = match.group(3)
        return f'{year}-{month}-{day}'

    # "on DD Mon YYYY" anywhere (e.g., "sold for $X on 06 Dec 2025")
    match = re.search(
        r'\bon\s+(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})',
        text,
        re.IGNORECASE,
    )
    if match:
        day = match.group(1).zfill(2)
        month = month_map.get(match.group(2).lower(), '01')
        year = match.group(3)
        return f'{year}-{month}-{day}'

    # "Sold DD/MM/YYYY"
    match = re.search(r'[Ss]old\s+(?:on\s+)?(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if match:
        day = match.group(1).zfill(2)
        month = match.group(2).zfill(2)
        year = match.group(3)
        return f'{year}-{month}-{day}'

    return None


def _deduplicate_results(listings: List[dict]) -> List[dict]:
    """Deduplicate listings by normalised address.

    When duplicates are found:
    - Prefer the source with highest domain priority (domain.com.au > realestate > allhomes)
    - Merge missing fields from lower-priority duplicates

    Args:
        listings: List of listing dicts from extract_listing_data

    Returns:
        Deduplicated list of listings
    """
    by_address: Dict[str, dict] = {}

    for listing in listings:
        addr = listing.get('address_normalised', '')
        if not addr:
            continue

        if addr not in by_address:
            by_address[addr] = listing
        else:
            existing = by_address[addr]
            existing_priority = DOMAIN_PRIORITY.get(existing.get('source_site', ''), 99)
            new_priority = DOMAIN_PRIORITY.get(listing.get('source_site', ''), 99)

            if new_priority < existing_priority:
                merged = _merge_listings(listing, existing)
                by_address[addr] = merged
            else:
                merged = _merge_listings(existing, listing)
                by_address[addr] = merged

    return list(by_address.values())


def _merge_listings(primary: dict, secondary: dict) -> dict:
    """Merge two listings, filling missing fields in primary from secondary."""
    merged = dict(primary)
    for key, value in secondary.items():
        if merged.get(key) is None and value is not None:
            merged[key] = value
    return merged


def fetch_sold_listings_google(
    suburb: str,
    property_type: str,
    postcode: str,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[int] = None,
) -> List[dict]:
    """Fetch sold listings by searching DuckDuckGo for domain.com.au results.

    Uses DuckDuckGo HTML version which works without JavaScript.
    Returns empty list on any error (graceful degradation).

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        postcode: 4-digit postcode
        bedrooms: Optional bedroom count filter
        bathrooms: Optional bathroom count filter

    Returns:
        List of parsed and deduplicated listing dicts
    """
    try:
        query = build_search_query(suburb, property_type, bedrooms, bathrooms)
        logger.info(f"DuckDuckGo search query: {query}")

        # Rate limiting: random delay between 2-5 seconds
        delay = random.uniform(2.0, 5.0)
        time.sleep(delay)

        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-AU,en;q=0.9',
            'Referer': 'https://html.duckduckgo.com/',
        }

        # DDG HTML version uses POST with form data
        data = {'q': query}

        response = requests.post(
            DDG_SEARCH_URL,
            headers=headers,
            data=data,
            timeout=30,
        )

        if response.status_code != 200:
            logger.warning(f"DuckDuckGo returned HTTP {response.status_code} for query: {query}")
            return []

        raw_results = parse_search_results_html(response.text)
        logger.info(f"Parsed {len(raw_results)} real estate results from DuckDuckGo")

        listings = []
        for result in raw_results:
            listing = extract_listing_data(result, suburb, postcode)
            listings.append(listing)

        deduplicated = _deduplicate_results(listings)
        logger.info(f"Returning {len(deduplicated)} deduplicated listings for {suburb}")

        return deduplicated

    except requests.RequestException as e:
        logger.error(f"DuckDuckGo search request failed: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error in DuckDuckGo search scraper: {e}")
        return []
