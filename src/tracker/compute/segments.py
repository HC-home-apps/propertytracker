# src/tracker/compute/segments.py
"""Market segment definitions for PropertyTracker.

Supports two modes:
1. Config-driven: Load segments from config.yml with area/street filters
2. Fallback: Use default hardcoded segments for backward compatibility
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set


@dataclass(frozen=True)
class Segment:
    """Market segment definition with optional filters."""

    code: str                           # Unique segment code
    display_name: str                   # Human-readable name for reports
    suburbs: FrozenSet[str]             # Suburbs included (lowercase)
    property_type: str                  # 'house' or 'unit'
    role: str                           # 'proxy' or 'target'
    description: str = ""               # Explanation shown in reports
    area_min: Optional[int] = None      # Min land area filter (sqm)
    area_max: Optional[int] = None      # Max land area filter (sqm)
    streets: Optional[FrozenSet[str]] = None  # Street name filter (lowercase)

    @property
    def is_proxy(self) -> bool:
        """Is this a proxy segment (your assets)?"""
        return self.role == 'proxy'

    @property
    def is_target(self) -> bool:
        """Is this a target market segment?"""
        return self.role == 'target'

    @property
    def has_filters(self) -> bool:
        """Does this segment have area or street filters?"""
        return self.area_min is not None or self.area_max is not None or self.streets is not None

    def get_filter_description(self) -> Optional[str]:
        """Get human-readable filter description for reports."""
        parts = []
        if self.area_min is not None or self.area_max is not None:
            if self.area_min and self.area_max:
                parts.append(f"{self.area_min}-{self.area_max}sqm land")
            elif self.area_min:
                parts.append(f"≥{self.area_min}sqm land")
            elif self.area_max:
                parts.append(f"≤{self.area_max}sqm land")
        if self.streets:
            street_list = '/'.join(sorted(s.title() for s in self.streets))
            parts.append(f"{street_list} streets")
        return ', '.join(parts) if parts else None


def load_segments_from_config(config: dict) -> Dict[str, Segment]:
    """
    Load segment definitions from config dictionary.

    Args:
        config: Parsed config.yml dictionary

    Returns:
        Dict mapping segment codes to Segment objects
    """
    segments_config = config.get('segments', {})
    if not segments_config:
        return get_default_segments()

    segments = {}
    for code, seg_config in segments_config.items():
        # Parse suburbs
        suburbs_list = seg_config.get('suburbs', [])
        suburbs = frozenset(s.lower().strip() for s in suburbs_list)

        # Parse filters
        filters = seg_config.get('filters', {})
        area_min = filters.get('area_min')
        area_max = filters.get('area_max')
        streets_list = filters.get('streets', [])
        streets = frozenset(s.lower().strip() for s in streets_list) if streets_list else None

        segment = Segment(
            code=code,
            display_name=seg_config.get('display_name', code),
            suburbs=suburbs,
            property_type=seg_config.get('property_type', 'house'),
            role=seg_config.get('role', 'target'),
            description=seg_config.get('description', ''),
            area_min=area_min,
            area_max=area_max,
            streets=streets,
        )
        segments[code] = segment

    return segments


def get_default_segments() -> Dict[str, Segment]:
    """
    Get default hardcoded segments for backward compatibility.

    Used when config.yml doesn't define segments.
    """
    return {
        'revesby_houses': Segment(
            code='revesby_houses',
            display_name='Revesby Houses',
            suburbs=frozenset({'revesby', 'revesby heights'}),
            property_type='house',
            role='proxy',
            description='Proxy for 11 Alliance Ave equity release capacity',
        ),
        'wollstonecraft_units': Segment(
            code='wollstonecraft_units',
            display_name='Wollstonecraft Units',
            suburbs=frozenset({'wollstonecraft'}),
            property_type='unit',
            role='proxy',
            description='Proxy for PPOR sale value (all units)',
        ),
        'lane_cove_houses': Segment(
            code='lane_cove_houses',
            display_name='Lane Cove Houses',
            suburbs=frozenset({'lane cove', 'lane cove north', 'lane cove west'}),
            property_type='house',
            role='target',
            description='Target market - houses',
        ),
        'chatswood_houses': Segment(
            code='chatswood_houses',
            display_name='Chatswood Houses',
            suburbs=frozenset({'chatswood', 'chatswood west'}),
            property_type='house',
            role='target',
            description='Target market - houses',
        ),
    }


# Global segments dict - populated at runtime from config or defaults
SEGMENTS: Dict[str, Segment] = get_default_segments()


def init_segments(config: dict) -> Dict[str, Segment]:
    """
    Initialize segments from config and update global SEGMENTS.

    Call this at startup after loading config.yml.

    Args:
        config: Parsed config.yml dictionary

    Returns:
        Dict of loaded segments
    """
    global SEGMENTS
    SEGMENTS = load_segments_from_config(config)
    return SEGMENTS


def get_segment(code: str) -> Optional[Segment]:
    """Get segment by code."""
    return SEGMENTS.get(code)


def get_segment_for_sale(
    suburb: str,
    property_type: str,
) -> Optional[str]:
    """
    Determine which segment a sale belongs to (basic match only).

    Note: This does NOT check area/street filters - those are applied
    at query time in metrics.py for performance.

    Args:
        suburb: Suburb name (case-insensitive)
        property_type: 'house' or 'unit'

    Returns:
        Segment code or None if not tracked
    """
    suburb_lower = suburb.lower().strip()

    for code, segment in SEGMENTS.items():
        if suburb_lower in segment.suburbs and segment.property_type == property_type:
            return code

    return None


def get_proxy_segments() -> List[Segment]:
    """Get segments that are proxies (your assets)."""
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
    Check if a property belongs to a specific segment (basic match only).

    Args:
        suburb: Suburb name
        property_type: 'house' or 'unit'
        segment_code: Segment to check

    Returns:
        True if property matches segment criteria (excluding area/street filters)
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


def get_outpacing_pairs(config: Optional[dict] = None) -> List[tuple]:
    """
    Get pairs of (proxy_segment, target_segment) for outpacing comparison.

    If config has gap_tracker section, uses that. Otherwise falls back
    to comparing each proxy against each target.

    Args:
        config: Optional config dict with gap_tracker section

    Returns:
        List of (proxy_code, target_code) tuples
    """
    if config and 'gap_tracker' in config:
        gap_config = config['gap_tracker']
        proxy_codes = gap_config.get('proxy_segments', [])
        target_code = gap_config.get('target_segment')
        secondary = gap_config.get('secondary_target')

        pairs = []
        for proxy_code in proxy_codes:
            if target_code:
                pairs.append((proxy_code, target_code))
            if secondary:
                pairs.append((proxy_code, secondary))
        return pairs

    # Fallback: all proxies vs all targets
    pairs = []
    proxies = get_proxy_segments()
    targets = get_target_segments()

    for proxy in proxies:
        for target in targets:
            # Only compare same property types
            if proxy.property_type == target.property_type:
                pairs.append((proxy.code, target.code))

    return pairs
