# src/tracker/compute/time_adjust.py
"""Time-adjusted median calculation for property valuations.

Adjusts older sales to current values using compound growth, then calculates
a weighted median that favors recent sales.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import statistics


@dataclass
class TimeAdjustedResult:
    """Result of time-adjusted median calculation."""

    # Core values
    naive_median: int              # Simple median without adjustment
    adjusted_median: int           # Time-adjusted median (base case)
    weighted_median: int           # Weighted by recency

    # Range estimates
    conservative_median: int       # Lower bound (lower growth assumption)
    optimistic_median: int         # Upper bound (higher growth assumption)

    # Metadata
    sample_size: int
    reference_date: date
    growth_rate_annual: float      # Base growth rate used
    oldest_sale_date: Optional[date]
    newest_sale_date: Optional[date]

    # Monthly breakdown
    sales_by_month: Dict[str, int]  # month -> count


@dataclass
class AdjustedSale:
    """Individual sale with time adjustment applied."""

    sale_id: str
    address: str
    original_price: int
    adjusted_price: int
    sale_date: date
    months_ago: int
    adjustment_pct: float
    recency_weight: float


def calculate_months_ago(sale_date: date, reference_date: date) -> int:
    """Calculate months between sale date and reference date."""
    return (reference_date.year - sale_date.year) * 12 + (reference_date.month - sale_date.month)


def apply_time_adjustment(
    price: int,
    months_ago: int,
    annual_growth_rate: float,
) -> Tuple[int, float]:
    """
    Apply compound growth adjustment to bring old price to current value.

    Args:
        price: Original sale price
        months_ago: Months since sale
        annual_growth_rate: Annual growth rate (e.g., 0.07 for 7%)

    Returns:
        Tuple of (adjusted_price, adjustment_percentage)
    """
    monthly_rate = annual_growth_rate / 12
    adjustment_factor = (1 + monthly_rate) ** months_ago
    adjusted_price = int(price * adjustment_factor)
    adjustment_pct = (adjustment_factor - 1) * 100
    return adjusted_price, adjustment_pct


def calculate_recency_weight(months_ago: int, decay_factor: float = 0.1) -> float:
    """
    Calculate recency weight - newer sales get higher weight.

    Args:
        months_ago: Months since sale
        decay_factor: How quickly weight decays (higher = faster decay)

    Returns:
        Weight between 0 and 1
    """
    return 1 / (1 + months_ago * decay_factor)


def weighted_median(values_and_weights: List[Tuple[int, float]]) -> int:
    """
    Calculate weighted median.

    Args:
        values_and_weights: List of (value, weight) tuples

    Returns:
        Weighted median value
    """
    if not values_and_weights:
        return 0

    # Sort by value
    sorted_vw = sorted(values_and_weights, key=lambda x: x[0])

    total_weight = sum(w for _, w in sorted_vw)
    cumulative = 0

    for value, weight in sorted_vw:
        cumulative += weight
        if cumulative >= total_weight / 2:
            return value

    return sorted_vw[-1][0]


def compute_time_adjusted_median(
    sales: List[Dict],
    reference_date: Optional[date] = None,
    base_growth_rate: float = 0.07,
    conservative_rate: float = 0.05,
    optimistic_rate: float = 0.10,
) -> TimeAdjustedResult:
    """
    Compute time-adjusted median from a list of sales.

    Args:
        sales: List of sale dicts with 'purchase_price', 'contract_date', 'sale_id', 'address'
        reference_date: Date to adjust all sales to (default: first of current month)
        base_growth_rate: Base annual growth assumption (default: 7%)
        conservative_rate: Lower bound growth rate (default: 5%)
        optimistic_rate: Upper bound growth rate (default: 10%)

    Returns:
        TimeAdjustedResult with all computed values
    """
    if not sales:
        return TimeAdjustedResult(
            naive_median=0,
            adjusted_median=0,
            weighted_median=0,
            conservative_median=0,
            optimistic_median=0,
            sample_size=0,
            reference_date=reference_date or date.today(),
            growth_rate_annual=base_growth_rate,
            oldest_sale_date=None,
            newest_sale_date=None,
            sales_by_month={},
        )

    if reference_date is None:
        reference_date = date.today().replace(day=1)

    # Parse dates and calculate adjustments
    adjusted_sales = []
    sales_by_month = defaultdict(int)

    for sale in sales:
        sale_date = sale['contract_date']
        if isinstance(sale_date, str):
            sale_date = datetime.strptime(sale_date, '%Y-%m-%d').date()

        months_ago = calculate_months_ago(sale_date, reference_date)
        month_key = sale_date.strftime('%Y-%m')
        sales_by_month[month_key] += 1

        adjusted_price, adj_pct = apply_time_adjustment(
            sale['purchase_price'],
            months_ago,
            base_growth_rate,
        )

        adjusted_sales.append(AdjustedSale(
            sale_id=sale.get('sale_id', ''),
            address=sale.get('address', ''),
            original_price=sale['purchase_price'],
            adjusted_price=adjusted_price,
            sale_date=sale_date,
            months_ago=months_ago,
            adjustment_pct=adj_pct,
            recency_weight=calculate_recency_weight(months_ago),
        ))

    # Sort by date for date range
    adjusted_sales.sort(key=lambda x: x.sale_date)
    oldest_date = adjusted_sales[0].sale_date
    newest_date = adjusted_sales[-1].sale_date

    # Naive median (no adjustment)
    naive_prices = sorted([s.original_price for s in adjusted_sales])
    mid = len(naive_prices) // 2
    if len(naive_prices) % 2 == 0:
        naive_median = (naive_prices[mid-1] + naive_prices[mid]) // 2
    else:
        naive_median = naive_prices[mid]

    # Base case adjusted median
    base_prices = sorted([s.adjusted_price for s in adjusted_sales])
    if len(base_prices) % 2 == 0:
        adjusted_median = (base_prices[mid-1] + base_prices[mid]) // 2
    else:
        adjusted_median = base_prices[mid]

    # Weighted median (by recency)
    values_weights = [(s.adjusted_price, s.recency_weight) for s in adjusted_sales]
    weighted_med = weighted_median(values_weights)

    # Conservative scenario
    conservative_adjusted = []
    for sale in adjusted_sales:
        adj_price, _ = apply_time_adjustment(
            sale.original_price,
            sale.months_ago,
            conservative_rate,
        )
        conservative_adjusted.append((adj_price, sale.recency_weight))
    conservative_med = weighted_median(conservative_adjusted)

    # Optimistic scenario
    optimistic_adjusted = []
    for sale in adjusted_sales:
        adj_price, _ = apply_time_adjustment(
            sale.original_price,
            sale.months_ago,
            optimistic_rate,
        )
        optimistic_adjusted.append((adj_price, sale.recency_weight))
    optimistic_med = weighted_median(optimistic_adjusted)

    return TimeAdjustedResult(
        naive_median=naive_median,
        adjusted_median=adjusted_median,
        weighted_median=weighted_med,
        conservative_median=conservative_med,
        optimistic_median=optimistic_med,
        sample_size=len(sales),
        reference_date=reference_date,
        growth_rate_annual=base_growth_rate,
        oldest_sale_date=oldest_date,
        newest_sale_date=newest_date,
        sales_by_month=dict(sales_by_month),
    )


def get_adjusted_sales_detail(
    sales: List[Dict],
    reference_date: Optional[date] = None,
    growth_rate: float = 0.07,
) -> List[AdjustedSale]:
    """
    Get detailed adjustment info for each sale.

    Args:
        sales: List of sale dicts
        reference_date: Date to adjust to
        growth_rate: Annual growth rate

    Returns:
        List of AdjustedSale objects with full details
    """
    if reference_date is None:
        reference_date = date.today().replace(day=1)

    adjusted = []
    for sale in sales:
        sale_date = sale['contract_date']
        if isinstance(sale_date, str):
            sale_date = datetime.strptime(sale_date, '%Y-%m-%d').date()

        months_ago = calculate_months_ago(sale_date, reference_date)
        adjusted_price, adj_pct = apply_time_adjustment(
            sale['purchase_price'],
            months_ago,
            growth_rate,
        )

        adjusted.append(AdjustedSale(
            sale_id=sale.get('sale_id', sale.get('dealing_number', '')),
            address=sale.get('address', ''),
            original_price=sale['purchase_price'],
            adjusted_price=adjusted_price,
            sale_date=sale_date,
            months_ago=months_ago,
            adjustment_pct=adj_pct,
            recency_weight=calculate_recency_weight(months_ago),
        ))

    return adjusted
