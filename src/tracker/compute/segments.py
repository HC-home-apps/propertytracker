# src/tracker/compute/segments.py
"""Market segment definitions for PropertyTracker.

Defines the market segments we track:
1. revesby_houses - Houses in Revesby (proxy for 11 Alliance Ave equity)
2. wollstonecraft_units - All units in Wollstonecraft
3. wollstonecraft_211 - 2/1/1 units in Wollstonecraft (comp basket)
4. lane_cove_houses - Houses in Lane Cove (target market)
5. lane_cove_units - Units in Lane Cove
6. chatswood_houses - Houses in Chatswood (target market)
7. chatswood_units - Units in Chatswood
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class Segment:
    """Market segment definition."""

    code: str                    # Unique segment code
    name: str                    # Human-readable name
    suburbs: Set[str]            # Suburbs included (lowercase)
    property_type: str           # 'house' or 'unit'
    is_target: bool = False      # Is this a target market we're buying into?
    is_proxy: bool = False       # Is this a proxy for a specific property?
    description: str = ""        # Additional context


# Define all tracked segments
SEGMENTS: Dict[str, Segment] = {
    'revesby_houses': Segment(
        code='revesby_houses',
        name='Revesby Houses',
        suburbs={'revesby', 'revesby heights'},
        property_type='house',
        is_proxy=True,
        description='Proxy for 11 Alliance Ave equity release capacity',
    ),
    'wollstonecraft_units': Segment(
        code='wollstonecraft_units',
        name='Wollstonecraft Units',
        suburbs={'wollstonecraft'},
        property_type='unit',
        is_proxy=True,
        description='Proxy for PPOR sale value (all units)',
    ),
    'wollstonecraft_211': Segment(
        code='wollstonecraft_211',
        name='Wollstonecraft 2/1/1 (Above Average)',
        suburbs={'wollstonecraft'},
        property_type='unit',
        is_proxy=True,
        description='Comp basket: 2bed/1bath/1car above-average quality',
    ),
    'lane_cove_houses': Segment(
        code='lane_cove_houses',
        name='Lane Cove Houses',
        suburbs={'lane cove', 'lane cove north', 'lane cove west'},
        property_type='house',
        is_target=True,
        description='Target market - houses',
    ),
    'lane_cove_units': Segment(
        code='lane_cove_units',
        name='Lane Cove Units',
        suburbs={'lane cove', 'lane cove north', 'lane cove west'},
        property_type='unit',
        is_target=True,
        description='Target market - units',
    ),
    'chatswood_houses': Segment(
        code='chatswood_houses',
        name='Chatswood Houses',
        suburbs={'chatswood', 'chatswood west'},
        property_type='house',
        is_target=True,
        description='Target market - houses',
    ),
    'chatswood_units': Segment(
        code='chatswood_units',
        name='Chatswood Units',
        suburbs={'chatswood', 'chatswood west'},
        property_type='unit',
        is_target=True,
        description='Target market - units',
    ),
}


def get_segment(code: str) -> Optional[Segment]:
    """Get segment by code."""
    return SEGMENTS.get(code)


def get_segment_for_sale(
    suburb: str,
    property_type: str,
) -> Optional[str]:
    """
    Determine which segment a sale belongs to.

    Args:
        suburb: Suburb name (case-insensitive)
        property_type: 'house' or 'unit'

    Returns:
        Segment code or None if not tracked
    """
    suburb_lower = suburb.lower().strip()

    for code, segment in SEGMENTS.items():
        # Skip the 211 comp basket (requires metadata match)
        if code == 'wollstonecraft_211':
            continue

        if suburb_lower in segment.suburbs and segment.property_type == property_type:
            return code

    return None


def get_proxy_segments() -> List[Segment]:
    """Get segments that are proxies (IP and PPOR)."""
    return [s for s in SEGMENTS.values() if s.is_proxy]


def get_target_segments() -> List[Segment]:
    """Get segments that are target markets."""
    return [s for s in SEGMENTS.values() if s.is_target]


def is_in_segment(
    suburb: str,
    property_type: str,
    segment_code: str,
) -> bool:
    """
    Check if a property belongs to a specific segment.

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        segment_code: Segment to check

    Returns:
        True if property matches segment criteria
    """
    segment = SEGMENTS.get(segment_code)
    if not segment:
        return False

    suburb_lower = suburb.lower().strip()
    return (
        suburb_lower in segment.suburbs
        and segment.property_type == property_type
    )


def get_all_tracked_suburbs() -> Set[str]:
    """Get all suburbs we track across all segments."""
    suburbs = set()
    for segment in SEGMENTS.values():
        suburbs.update(segment.suburbs)
    return suburbs


def get_outpacing_pairs() -> List[tuple]:
    """
    Get pairs of (proxy_segment, target_segment) for outpacing comparison.

    Returns:
        List of (proxy_code, target_code) tuples
    """
    pairs = []

    # Compare Revesby houses against target houses
    for target_code in ['lane_cove_houses', 'chatswood_houses']:
        pairs.append(('revesby_houses', target_code))

    # Compare Wollstonecraft units against target units
    for target_code in ['lane_cove_units', 'chatswood_units']:
        pairs.append(('wollstonecraft_units', target_code))

    return pairs
