# src/tracker/compute/gap_tracker.py
"""Gap Tracker: Calculate if combined assets are catching up to target.

This module computes whether your combined property assets (Revesby IP +
Wollstonecraft PPOR) are growing faster or slower than your target market
(Lane Cove / Chatswood houses).

Key metrics:
- Combined proxy change: Sum of YoY dollar changes across all your assets
- Target change: YoY dollar change of target market
- Net position: Are you catching up or falling behind?
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from tracker.compute.metrics import MetricResult


@dataclass
class GapTrackerResult:
    """Result of gap tracker calculation."""

    # Combined proxy metrics
    proxy_total_change: Optional[int]  # Sum of YoY dollar changes
    proxy_breakdown: Dict[str, Dict]   # Per-segment breakdown

    # Target metrics
    target_segment: str
    target_display_name: str
    target_median: Optional[int]
    target_yoy: Optional[float]
    target_change: Optional[int]

    # Gap analysis
    net_position: Optional[int]        # Positive = catching up
    is_catching_up: Optional[bool]

    # For reporting
    can_compute: bool
    computation_notes: List[str]


def compute_gap_tracker(
    proxy_metrics: Dict[str, MetricResult],
    target_metric: MetricResult,
    config: Optional[dict] = None,
) -> GapTrackerResult:
    """
    Calculate if combined proxy assets are catching up to target.

    This is the core gap tracker calculation:
    1. Sum up YoY dollar changes for all proxy segments (your assets)
    2. Calculate YoY dollar change for target segment
    3. Compare: positive net_position = you're catching up

    Args:
        proxy_metrics: Dict of segment_code -> MetricResult for proxy segments
        target_metric: MetricResult for target segment
        config: Optional config dict for display preferences

    Returns:
        GapTrackerResult with calculation details
    """
    notes = []

    # Calculate proxy breakdown
    proxy_breakdown = {}
    proxy_total_change = 0
    has_valid_proxy = False

    for code, metric in proxy_metrics.items():
        entry = {
            'display_name': metric.display_name or code,
            'median': metric.median_price,
            'yoy_pct': metric.yoy_pct,
            'change': None,
            'sample_size': metric.sample_size,
            'is_suppressed': metric.is_suppressed,
            'filter_description': metric.filter_description,
        }

        if metric.is_suppressed:
            notes.append(f"{entry['display_name']}: suppressed ({metric.suppression_reason})")
        elif metric.median_price is not None and metric.yoy_pct is not None:
            change = int(metric.median_price * (metric.yoy_pct / 100))
            entry['change'] = change
            proxy_total_change += change
            has_valid_proxy = True
        else:
            notes.append(f"{entry['display_name']}: missing YoY data")

        proxy_breakdown[code] = entry

    # Calculate target change
    target_change = None
    if target_metric.is_suppressed:
        notes.append(f"Target {target_metric.display_name}: suppressed")
    elif target_metric.median_price is not None and target_metric.yoy_pct is not None:
        target_change = int(target_metric.median_price * (target_metric.yoy_pct / 100))
    else:
        notes.append(f"Target {target_metric.display_name}: missing YoY data")

    # Calculate net position
    net_position = None
    is_catching_up = None
    can_compute = has_valid_proxy and target_change is not None

    if can_compute:
        net_position = proxy_total_change - target_change
        is_catching_up = net_position > 0

    return GapTrackerResult(
        proxy_total_change=proxy_total_change if has_valid_proxy else None,
        proxy_breakdown=proxy_breakdown,
        target_segment=target_metric.segment,
        target_display_name=target_metric.display_name or target_metric.segment,
        target_median=target_metric.median_price,
        target_yoy=target_metric.yoy_pct,
        target_change=target_change,
        net_position=net_position,
        is_catching_up=is_catching_up,
        can_compute=can_compute,
        computation_notes=notes,
    )


def format_gap_tracker_summary(result: GapTrackerResult) -> str:
    """
    Format gap tracker result as a summary string for reports.

    Args:
        result: GapTrackerResult to format

    Returns:
        Formatted summary string
    """
    if not result.can_compute:
        return "Gap tracker: Cannot compute (insufficient data)"

    lines = []

    # Proxy breakdown
    proxy_parts = []
    for code, entry in result.proxy_breakdown.items():
        if entry['change'] is not None:
            sign = '+' if entry['change'] >= 0 else ''
            proxy_parts.append(f"{entry['display_name']} {sign}${entry['change']:,}")

    lines.append(f"Your assets this year: ${result.proxy_total_change:+,}")
    if proxy_parts:
        lines.append(f"  ({', '.join(proxy_parts)})")

    # Target
    if result.target_change is not None:
        lines.append(f"Target ({result.target_display_name}): ${result.target_change:+,}")

    # Net position
    if result.net_position is not None:
        if result.is_catching_up:
            lines.append(f"Catching up by ${result.net_position:,}/year")
        else:
            lines.append(f"Falling behind by ${abs(result.net_position):,}/year")

    return '\n'.join(lines)


def get_gap_tracker_verdict(result: GapTrackerResult) -> str:
    """
    Generate a verdict message for the gap tracker.

    Args:
        result: GapTrackerResult

    Returns:
        Verdict string
    """
    if not result.can_compute:
        return "Insufficient data to calculate gap trend."

    if result.is_catching_up:
        if result.net_position and result.net_position > 50000:
            return "Strong progress! Your assets are significantly outpacing the target market."
        else:
            return "Positive trend. Your assets are growing faster than the target."
    else:
        if result.net_position and abs(result.net_position) > 50000:
            return "Gap widening. Target market growing significantly faster than your assets."
        else:
            return "Slight negative trend. Target market growing faster than your assets."
