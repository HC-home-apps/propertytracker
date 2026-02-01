# src/tracker/enrich/classifier.py
"""Classification logic for auto-excluding non-comparable sales."""

from typing import Optional, Tuple

# Keywords that indicate property is not a comparable (already developed/modern)
EXCLUDE_KEYWORDS = [
    'duplex',
    'dual occ',
    'torrens',
    'brand new',
    'just completed',
]

# Allowed zoning codes for duplex development potential
ALLOWED_ZONINGS = {'R2', 'R3'}

# Year built threshold - exclude modern builds
YEAR_BUILT_THRESHOLD = 2010


def has_exclude_keywords(description: Optional[str]) -> bool:
    """
    Check if description contains keywords indicating non-comparable.

    Args:
        description: Property description text (can be None)

    Returns:
        True if any exclude keywords found
    """
    if not description:
        return False

    desc_lower = description.lower()
    return any(kw in desc_lower for kw in EXCLUDE_KEYWORDS)


def should_auto_exclude(
    zoning: Optional[str],
    year_built: Optional[int],
    has_keywords: bool,
) -> Tuple[bool, Optional[str]]:
    """
    Determine if a sale should be auto-excluded from comparables.

    Args:
        zoning: Property zoning code (R2, R3, B1, etc.)
        year_built: Year property was built
        has_keywords: Whether exclude keywords were found in description

    Returns:
        Tuple of (is_excluded, reason)
        - is_excluded: True if should be auto-excluded
        - reason: Human-readable reason for exclusion, or None if not excluded
    """
    # Check zoning (if known)
    if zoning and zoning not in ALLOWED_ZONINGS:
        return True, f"non-R2 zoning ({zoning})"

    # Check year built (if known)
    if year_built and year_built > YEAR_BUILT_THRESHOLD:
        return True, f"modern build ({year_built})"

    # Check keywords
    if has_keywords:
        return True, "existing duplex"

    return False, None
