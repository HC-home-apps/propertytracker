# src/tracker/review/telegram.py
"""Telegram review message formatting and reply parsing."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from tracker.db import Database


def format_currency(amount: int) -> str:
    """Format amount as currency string."""
    return f"${amount:,}"


def format_domain_url(address: str, suburb: str) -> str:
    """
    Generate Domain.com.au search URL for a property.

    Args:
        address: Street address (e.g., "15 Smith St")
        suburb: Suburb name

    Returns:
        Domain search URL
    """
    # Clean and encode address for URL
    search_term = f"{address} {suburb} NSW".lower().replace(' ', '-')
    return f"https://www.domain.com.au/{quote(search_term)}"


def format_review_message(sales: List[Dict[str, Any]]) -> str:
    """
    Format sales for Telegram review message.

    Args:
        sales: List of sale dicts with keys:
            - sale_id, address, price, area_sqm, zoning, year_built

    Returns:
        Formatted Telegram message (HTML)
    """
    lines = [f"📋 {len(sales)} Revesby sale{'s' if len(sales) != 1 else ''} need review:\n"]

    for i, sale in enumerate(sales, 1):
        address = sale.get('address', 'Unknown')
        price = format_currency(sale.get('price', 0))
        area = sale.get('area_sqm', 0)
        zoning = sale.get('zoning', 'Unknown')
        year = sale.get('year_built')

        # Format year info
        year_str = f"Built {year}" if year else "Year unknown"

        # Build Domain URL
        # Extract street part for URL
        street_part = address.split(',')[0] if ',' in address else address
        suburb = 'Revesby'
        domain_url = format_domain_url(street_part, suburb)

        lines.append(f"{i}. <b>{street_part}</b> - {price} ({area:.0f}sqm)")
        lines.append(f"   🏷️ {zoning} | {year_str}")
        lines.append(f"   🔗 {domain_url}")
        lines.append("")

    # Add reply instructions
    lines.append("Reply: <code>1✅ 2✅ 3❌</code>")
    lines.append("(or just <code>✅✅❌</code> or <code>all✅</code>)")

    return "\n".join(lines)


def parse_review_reply(text: str, sale_count: int) -> Optional[List[str]]:
    """
    Parse user reply into list of statuses.

    Args:
        text: User's reply text
        sale_count: Number of sales being reviewed

    Returns:
        List of statuses ['comparable', 'not_comparable', 'pending', ...]
        Returns None if reply is invalid/incomplete
    """
    text = text.strip().lower()

    # Handle special commands
    if text == 'skip':
        return ['pending'] * sale_count

    if text.startswith('all'):
        if '✅' in text:
            return ['comparable'] * sale_count
        if '❌' in text:
            return ['not_comparable'] * sale_count

    # Extract emojis in order
    statuses = []

    # Try numbered format first: "1✅ 2❌ 3✅"
    numbered_pattern = re.findall(r'(\d+)\s*([✅❌])', text)
    if numbered_pattern:
        # Build ordered list from numbered responses
        result = ['pending'] * sale_count
        for num_str, emoji in numbered_pattern:
            idx = int(num_str) - 1
            if 0 <= idx < sale_count:
                result[idx] = 'comparable' if emoji == '✅' else 'not_comparable'

        # Check all were specified
        if result.count('pending') == 0:
            return result

    # Try simple emoji sequence: "✅✅❌"
    emojis = re.findall(r'[✅❌]', text)
    if len(emojis) == sale_count:
        return [
            'comparable' if e == '✅' else 'not_comparable'
            for e in emojis
        ]

    # Invalid reply
    return None


def update_review_statuses(
    db: Database,
    sale_ids: List[str],
    statuses: List[str],
) -> int:
    """
    Update review statuses for sales in database.

    Args:
        db: Database connection
        sale_ids: List of sale IDs to update
        statuses: Corresponding list of statuses ('comparable', 'not_comparable', 'pending')

    Returns:
        Number of records updated
    """
    if len(sale_ids) != len(statuses):
        raise ValueError("sale_ids and statuses must have same length")

    updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for sale_id, status in zip(sale_ids, statuses):
        # Determine use_in_median based on status
        use_in_median = 1 if status == 'comparable' else 0

        result = db.execute("""
            UPDATE sale_classifications
            SET review_status = ?,
                use_in_median = ?,
                reviewed_at = ?,
                updated_at = ?
            WHERE sale_id = ?
        """, (status, use_in_median, now, now, sale_id))

        if result > 0:
            updated += 1

    return updated
