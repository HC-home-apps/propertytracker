# src/tracker/compute/metrics.py
"""Metrics computation for PropertyTracker.

Computes:
- Rolling 3-month median prices
- Year-over-year percentage change
- Outpacing metrics (% and $ spreads)
- Sample size validation with automatic fallbacks
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from tracker.db import Database
from tracker.compute.segments import SEGMENTS, get_segment

logger = logging.getLogger(__name__)

# Sample size thresholds for statistical validity
MIN_SAMPLE_MONTHLY = 3
MIN_SAMPLE_QUARTERLY = 5
MIN_SAMPLE_6MONTH = 8


@dataclass
class MetricResult:
    """Result of metric computation for a segment/period."""

    segment: str
    period_start: date
    period_end: date
    period_type: str  # 'monthly', 'quarterly', '6month'
    median_price: Optional[int]
    sample_size: int
    yoy_pct: Optional[float]
    rolling_median_3m: Optional[int]
    rolling_sample_3m: Optional[int]
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None


def compute_median(prices: List[int]) -> Optional[int]:
    """Compute median of price list, returning None if empty."""
    if not prices:
        return None
    return int(np.median(prices))


def compute_yoy_change(current: Optional[int], prior: Optional[int]) -> Optional[float]:
    """
    Compute year-over-year percentage change.

    Returns:
        Percentage change (e.g., 5.2 for 5.2%) or None if not computable
    """
    if current is None or prior is None or prior == 0:
        return None
    return round(((current - prior) / prior) * 100, 1)


def get_period_sales(
    db: Database,
    segment_code: str,
    start_date: date,
    end_date: date,
) -> List[int]:
    """
    Get sale prices for a segment within a date range.

    Args:
        db: Database connection
        segment_code: Segment to query
        start_date: Period start (inclusive)
        end_date: Period end (inclusive)

    Returns:
        List of sale prices
    """
    segment = get_segment(segment_code)
    if not segment:
        return []

    # Build suburb list for SQL
    suburbs = list(segment.suburbs)
    placeholders = ','.join(['?' for _ in suburbs])

    query = f"""
        SELECT purchase_price
        FROM raw_sales
        WHERE LOWER(suburb) IN ({placeholders})
          AND property_type = ?
          AND contract_date BETWEEN ? AND ?
          AND purchase_price > 0
    """

    params = tuple(suburbs) + (segment.property_type, start_date.isoformat(), end_date.isoformat())
    rows = db.query(query, params)

    return [row['purchase_price'] for row in rows]


def compute_segment_metrics(
    db: Database,
    segment_code: str,
    reference_date: date,
    min_sample_monthly: int = MIN_SAMPLE_MONTHLY,
    min_sample_quarterly: int = MIN_SAMPLE_QUARTERLY,
    min_sample_6month: int = MIN_SAMPLE_6MONTH,
) -> MetricResult:
    """
    Compute metrics for a segment at a reference date.

    Uses automatic fallback from monthly → quarterly → 6-month if sample
    size is insufficient.

    Args:
        db: Database connection
        segment_code: Segment to compute
        reference_date: End date for the period
        min_sample_*: Minimum sample sizes for each period type

    Returns:
        MetricResult with computed values or suppression
    """
    # Try monthly first
    monthly_start = reference_date.replace(day=1)
    monthly_end = reference_date

    prices = get_period_sales(db, segment_code, monthly_start, monthly_end)

    if len(prices) >= min_sample_monthly:
        return _compute_with_period(
            db, segment_code, monthly_start, monthly_end,
            prices, 'monthly', reference_date
        )

    # Fallback to quarterly
    quarterly_start = (reference_date - timedelta(days=90)).replace(day=1)
    prices = get_period_sales(db, segment_code, quarterly_start, monthly_end)

    if len(prices) >= min_sample_quarterly:
        return _compute_with_period(
            db, segment_code, quarterly_start, monthly_end,
            prices, 'quarterly', reference_date
        )

    # Fallback to 6-month
    sixmonth_start = (reference_date - timedelta(days=180)).replace(day=1)
    prices = get_period_sales(db, segment_code, sixmonth_start, monthly_end)

    if len(prices) >= min_sample_6month:
        return _compute_with_period(
            db, segment_code, sixmonth_start, monthly_end,
            prices, '6month', reference_date
        )

    # Suppress if still insufficient
    return MetricResult(
        segment=segment_code,
        period_start=monthly_start,
        period_end=monthly_end,
        period_type='monthly',
        median_price=None,
        sample_size=len(prices),
        yoy_pct=None,
        rolling_median_3m=None,
        rolling_sample_3m=None,
        is_suppressed=True,
        suppression_reason=f"Insufficient sample size: {len(prices)} < {min_sample_6month}",
    )


def _compute_with_period(
    db: Database,
    segment_code: str,
    period_start: date,
    period_end: date,
    prices: List[int],
    period_type: str,
    reference_date: date,
) -> MetricResult:
    """Compute full metrics for a valid period."""
    median = compute_median(prices)

    # Get prior year median for YoY
    prior_start = period_start.replace(year=period_start.year - 1)
    prior_end = period_end.replace(year=period_end.year - 1)
    prior_prices = get_period_sales(db, segment_code, prior_start, prior_end)
    prior_median = compute_median(prior_prices)

    yoy = compute_yoy_change(median, prior_median)

    # Compute 3-month rolling
    rolling_start = (reference_date - timedelta(days=90)).replace(day=1)
    rolling_prices = get_period_sales(db, segment_code, rolling_start, reference_date)
    rolling_median = compute_median(rolling_prices)

    return MetricResult(
        segment=segment_code,
        period_start=period_start,
        period_end=period_end,
        period_type=period_type,
        median_price=median,
        sample_size=len(prices),
        yoy_pct=yoy,
        rolling_median_3m=rolling_median,
        rolling_sample_3m=len(rolling_prices),
        is_suppressed=False,
        suppression_reason=None,
    )


def compute_outpacing_metrics(
    proxy_metrics: MetricResult,
    target_metrics: MetricResult,
) -> Dict:
    """
    Compute outpacing metrics between proxy and target segments.

    Args:
        proxy_metrics: Metrics for proxy segment (e.g., Revesby houses)
        target_metrics: Metrics for target segment (e.g., Lane Cove houses)

    Returns:
        Dict with outpacing calculations
    """
    result = {
        'proxy_segment': proxy_metrics.segment,
        'target_segment': target_metrics.segment,
        'pct_spread': None,
        'dollar_spread': None,
        'proxy_yoy': proxy_metrics.yoy_pct,
        'target_yoy': target_metrics.yoy_pct,
        'is_outpacing': None,
    }

    # Percentage spread (proxy YoY minus target YoY)
    if proxy_metrics.yoy_pct is not None and target_metrics.yoy_pct is not None:
        result['pct_spread'] = round(proxy_metrics.yoy_pct - target_metrics.yoy_pct, 1)
        result['is_outpacing'] = result['pct_spread'] > 0

    # Dollar spread (change in proxy value vs change in target value)
    if (proxy_metrics.median_price is not None and
        target_metrics.median_price is not None and
        proxy_metrics.yoy_pct is not None and
        target_metrics.yoy_pct is not None):

        # Approximate dollar change based on YoY %
        proxy_change = int(proxy_metrics.median_price * (proxy_metrics.yoy_pct / 100))
        target_change = int(target_metrics.median_price * (target_metrics.yoy_pct / 100))
        result['dollar_spread'] = proxy_change - target_change

    return result


def compute_all_metrics(
    db: Database,
    reference_date: date,
    thresholds: Optional[Dict] = None,
) -> Dict[str, MetricResult]:
    """
    Compute metrics for all segments.

    Args:
        db: Database connection
        reference_date: End date for metrics
        thresholds: Optional dict with min_sample_* overrides

    Returns:
        Dict mapping segment codes to MetricResult
    """
    if thresholds is None:
        thresholds = {}

    results = {}
    for segment_code in SEGMENTS:
        # Skip 211 comp basket (requires metadata)
        if segment_code == 'wollstonecraft_211':
            continue

        results[segment_code] = compute_segment_metrics(
            db,
            segment_code,
            reference_date,
            min_sample_monthly=thresholds.get('min_sample_monthly', MIN_SAMPLE_MONTHLY),
            min_sample_quarterly=thresholds.get('min_sample_quarterly', MIN_SAMPLE_QUARTERLY),
            min_sample_6month=thresholds.get('min_sample_6month', MIN_SAMPLE_6MONTH),
        )

    return results


def save_metrics_to_db(db: Database, metrics: Dict[str, MetricResult]) -> int:
    """
    Save computed metrics to monthly_metrics table.

    Args:
        db: Database connection
        metrics: Dict of segment code → MetricResult

    Returns:
        Number of records saved
    """
    saved = 0

    for segment_code, result in metrics.items():
        db.execute(
            """
            INSERT OR REPLACE INTO monthly_metrics (
                period_start, period_end, period_type, segment,
                median_price, sample_size, yoy_pct,
                rolling_median_3m, rolling_sample_3m,
                is_suppressed, suppression_reason, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.period_start.isoformat(),
                result.period_end.isoformat(),
                result.period_type,
                segment_code,
                result.median_price,
                result.sample_size,
                result.yoy_pct,
                result.rolling_median_3m,
                result.rolling_sample_3m,
                result.is_suppressed,
                result.suppression_reason,
                datetime.now(timezone.utc).isoformat(),
            )
        )
        saved += 1

    return saved
