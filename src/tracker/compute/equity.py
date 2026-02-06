# src/tracker/compute/equity.py
"""Equity and affordability gap calculator for PropertyTracker.

Computes:
- Usable equity from investment property (Revesby)
- Net proceeds from PPOR sale (Wollstonecraft)
- Total purchasing power
- Affordability gap to target markets
- Scenario bands (bear/base/bull)
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class EquityScenario:
    """Equity calculation for a single scenario (bear/base/bull)."""

    scenario: str  # 'bear', 'base', 'bull'

    # Investment property (Revesby)
    ip_proxy_value: int           # Proxy market value
    ip_valuation: int             # Value after haircut
    ip_debt: int                  # Current debt
    ip_gross_equity: int          # valuation - debt
    ip_usable_equity: int         # Capped at LVR limit

    # PPOR (Wollstonecraft)
    ppor_proxy_value: int         # Proxy market value
    ppor_selling_costs: int       # Transaction costs
    ppor_debt: int                # Debt to clear
    ppor_net_proceeds: int        # net after costs and debt

    # Savings
    savings_balance: int
    monthly_savings: int

    # Total purchasing power
    total_cash: int               # savings + ppor proceeds + usable equity

    # Target purchase
    target_price: int             # Target market median
    stamp_duty: int               # NSW stamp duty
    purchase_costs: int           # Additional costs (legals, etc.)
    total_purchase_cost: int      # price + stamp + costs

    # Gap
    affordability_gap: int        # total_purchase_cost - total_cash


@dataclass
class AffordabilityResult:
    """Complete affordability analysis with all scenarios."""

    bear: EquityScenario
    base: EquityScenario
    bull: EquityScenario

    # Summary
    gap_range: tuple  # (worst_gap, best_gap)
    is_affordable: bool  # True if base case gap <= 0
    months_to_close_gap: Optional[int]  # At current savings rate


def compute_nsw_stamp_duty(price: int) -> int:
    """
    Compute NSW stamp duty for property purchase.

    Uses standard rates (not first home buyer concessions).

    Args:
        price: Purchase price in dollars

    Returns:
        Stamp duty in dollars
    """
    # NSW stamp duty rates (2024)
    # $0 - $16,000: 1.25%
    # $16,001 - $35,000: $200 + 1.5% of excess
    # $35,001 - $93,000: $485 + 1.75% of excess
    # $93,001 - $351,000: $1,500 + 3.5% of excess
    # $351,001 - $1,168,000: $10,530 + 4.5% of excess
    # $1,168,001+: $47,295 + 5.5% of excess

    if price <= 16000:
        return int(price * 0.0125)
    elif price <= 35000:
        return int(200 + (price - 16000) * 0.015)
    elif price <= 93000:
        return int(485 + (price - 35000) * 0.0175)
    elif price <= 351000:
        return int(1500 + (price - 93000) * 0.035)
    elif price <= 1168000:
        return int(10530 + (price - 351000) * 0.045)
    else:
        return int(47295 + (price - 1168000) * 0.055)


def compute_usable_equity(
    market_value: int,
    valuation_haircut: float,
    current_debt: int,
    lvr_cap: float,
) -> tuple:
    """
    Compute usable equity from a property.

    Args:
        market_value: Proxy market value
        valuation_haircut: Haircut factor (e.g., 0.95 for 5% haircut)
        current_debt: Current mortgage balance
        lvr_cap: Maximum LVR for refinance (e.g., 0.80)

    Returns:
        Tuple of (valuation, gross_equity, usable_equity)
    """
    valuation = int(market_value * valuation_haircut)
    gross_equity = max(0, valuation - current_debt)

    # Usable equity = what we can borrow up to LVR cap, minus current debt
    max_borrowing = int(valuation * lvr_cap)
    usable_equity = max(0, max_borrowing - current_debt)

    return valuation, gross_equity, usable_equity


def compute_ppor_proceeds(
    sale_price: int,
    selling_cost_rate: float,
    debt_to_clear: int,
) -> tuple:
    """
    Compute net proceeds from PPOR sale.

    Args:
        sale_price: Expected sale price
        selling_cost_rate: Cost rate (e.g., 0.02 for 2%)
        debt_to_clear: Mortgage to pay off

    Returns:
        Tuple of (selling_costs, net_proceeds)
    """
    selling_costs = int(sale_price * selling_cost_rate)
    net_proceeds = max(0, sale_price - selling_costs - debt_to_clear)

    return selling_costs, net_proceeds


def compute_affordability_gap(
    config: Dict,
    ip_proxy_value: int,
    ppor_proxy_value: int,
    target_price: int,
) -> AffordabilityResult:
    """
    Compute full affordability analysis with scenario bands.

    Args:
        config: Configuration dict with financial parameters
        ip_proxy_value: Investment property proxy value (Revesby)
        ppor_proxy_value: PPOR proxy value (Wollstonecraft)
        target_price: Target market median price

    Returns:
        AffordabilityResult with all scenarios
    """
    savings_balance = config['savings']['current_balance']
    monthly_savings = config['savings']['monthly_contribution']

    ppor_debt = config['ppor']['debt']
    ppor_selling_rate = config['ppor']['selling_cost_rate']

    ip_debt = config['investment_property']['debt']
    lvr_cap = config['investment_property']['refinance_lvr_cap']
    haircuts = config['investment_property']['valuation_haircut']

    purchase_cost_rate = config.get('purchase_costs', {}).get('rate', 0.01)

    scenarios = {}

    for scenario_name, haircut in [
        ('bear', haircuts['bear']),
        ('base', haircuts['base']),
        ('bull', haircuts['bull']),
    ]:
        # Investment property equity
        ip_valuation, ip_gross, ip_usable = compute_usable_equity(
            ip_proxy_value, haircut, ip_debt, lvr_cap
        )

        # PPOR sale proceeds (use same haircut for consistency)
        ppor_adjusted = int(ppor_proxy_value * haircut)
        ppor_costs, ppor_net = compute_ppor_proceeds(
            ppor_adjusted, ppor_selling_rate, ppor_debt
        )

        # Total purchasing power
        total_cash = savings_balance + ppor_net + ip_usable

        # Target purchase costs
        stamp_duty = compute_nsw_stamp_duty(target_price)
        purchase_costs = int(target_price * purchase_cost_rate)
        total_purchase = target_price + stamp_duty + purchase_costs

        # Gap
        gap = total_purchase - total_cash

        scenarios[scenario_name] = EquityScenario(
            scenario=scenario_name,
            ip_proxy_value=ip_proxy_value,
            ip_valuation=ip_valuation,
            ip_debt=ip_debt,
            ip_gross_equity=ip_gross,
            ip_usable_equity=ip_usable,
            ppor_proxy_value=ppor_proxy_value,
            ppor_selling_costs=ppor_costs,
            ppor_debt=ppor_debt,
            ppor_net_proceeds=ppor_net,
            savings_balance=savings_balance,
            monthly_savings=monthly_savings,
            total_cash=total_cash,
            target_price=target_price,
            stamp_duty=stamp_duty,
            purchase_costs=purchase_costs,
            total_purchase_cost=total_purchase,
            affordability_gap=gap,
        )

    # Compute summary
    gaps = [s.affordability_gap for s in scenarios.values()]
    gap_range = (max(gaps), min(gaps))  # worst to best
    is_affordable = scenarios['base'].affordability_gap <= 0

    # Months to close gap (base case, if gap > 0)
    base_gap = scenarios['base'].affordability_gap
    if base_gap > 0 and monthly_savings > 0:
        months_to_close = int(base_gap / monthly_savings) + 1
    else:
        months_to_close = None

    return AffordabilityResult(
        bear=scenarios['bear'],
        base=scenarios['base'],
        bull=scenarios['bull'],
        gap_range=gap_range,
        is_affordable=is_affordable,
        months_to_close_gap=months_to_close,
    )


def format_currency(amount: int) -> str:
    """Format amount as Australian currency."""
    if amount is None:
        return "N/A"
    if amount >= 0:
        return f"${amount:,}"
    else:
        return f"-${abs(amount):,}"


def format_gap_summary(result: AffordabilityResult) -> str:
    """Format affordability gap for display."""
    bear = result.bear.affordability_gap
    base = result.base.affordability_gap
    bull = result.bull.affordability_gap

    lines = [
        "Affordability Gap Analysis:",
        f"  Bear: {format_currency(bear)}",
        f"  Base: {format_currency(base)}",
        f"  Bull: {format_currency(bull)}",
    ]

    if result.is_affordable:
        lines.append("  Status: AFFORDABLE (base case)")
    else:
        lines.append(f"  Status: Gap of {format_currency(base)}")
        if result.months_to_close_gap:
            lines.append(f"  Time to close: ~{result.months_to_close_gap} months")

    return '\n'.join(lines)
