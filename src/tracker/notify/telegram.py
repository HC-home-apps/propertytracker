# src/tracker/notify/telegram.py
"""Telegram notification integration for PropertyTracker.

Sends fortnightly reports and ad-hoc notifications via Telegram bot.

Report format includes:
- Your Properties section with filter explanations
- Target Markets section
- Gap Tracker (combined asset growth vs target)
- Affordability Gap calculation
- Verdict summary
"""

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import requests

from tracker.compute.metrics import MetricResult
from tracker.compute.equity import AffordabilityResult, format_currency
from tracker.compute.gap_tracker import GapTrackerResult
from tracker.compute.segments import SEGMENTS, get_segment

logger = logging.getLogger(__name__)

# Telegram Bot API base URL
TELEGRAM_API_BASE = "https://api.telegram.org/bot"


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""

    bot_token: str
    chat_id: str

    @classmethod
    def from_env(cls) -> 'TelegramConfig':
        """Load config from environment variables."""
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')

        if not token or not chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set"
            )

        return cls(bot_token=token, chat_id=chat_id)


def send_message(
    config: TelegramConfig,
    message: str,
    parse_mode: str = 'HTML',
) -> bool:
    """
    Send a message via Telegram bot.

    Args:
        config: Telegram configuration
        message: Message text (HTML or plain)
        parse_mode: 'HTML' or 'MarkdownV2'

    Returns:
        True if successful, False otherwise
    """
    url = f"{TELEGRAM_API_BASE}{config.bot_token}/sendMessage"

    payload = {
        'chat_id': config.chat_id,
        'text': message,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True,
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def format_metric_line(
    metric: MetricResult,
    include_filter: bool = True,
) -> str:
    """
    Format a single metric line for Telegram.

    Args:
        metric: MetricResult to format
        include_filter: Whether to include filter description

    Returns:
        Formatted metric line
    """
    label = metric.display_name or metric.segment

    if metric.is_suppressed:
        return f"• {label}: Insufficient data ({metric.suppression_reason})"

    median = format_currency(metric.median_price) if metric.median_price else "N/A"
    yoy = f"{metric.yoy_pct:+.1f}%" if metric.yoy_pct is not None else "N/A"
    n = metric.sample_size

    period_note = ""
    if metric.period_type != 'monthly':
        period_note = f" ({metric.period_type})"

    line = f"• {label}: {median} ({yoy}, n={n}){period_note}"

    # Add filter description if available
    if include_filter and metric.filter_description:
        line += f"\n  Filtered: {metric.filter_description}"

    return line


def format_gap_tracker_section(gap_tracker: GapTrackerResult) -> List[str]:
    """Format the gap tracker section of the report."""
    lines = []

    if not gap_tracker.can_compute:
        lines.append("Cannot compute (insufficient data)")
        if gap_tracker.computation_notes:
            for note in gap_tracker.computation_notes:
                lines.append(f"  • {note}")
        return lines

    # Proxy breakdown
    if gap_tracker.proxy_total_change is not None:
        sign = '+' if gap_tracker.proxy_total_change >= 0 else ''
        lines.append(f"Your assets this year: {sign}${gap_tracker.proxy_total_change:,}")

        # Individual breakdown
        breakdown_parts = []
        for code, entry in gap_tracker.proxy_breakdown.items():
            if entry['change'] is not None:
                name = entry['display_name'].split(' (')[0]  # Shorter name
                sign = '+' if entry['change'] >= 0 else ''
                breakdown_parts.append(f"{name} {sign}${entry['change']:,}")

        if breakdown_parts:
            lines.append(f"  ({', '.join(breakdown_parts)})")

    # Target change
    if gap_tracker.target_change is not None:
        sign = '+' if gap_tracker.target_change >= 0 else ''
        lines.append(f"Target ({gap_tracker.target_display_name}): {sign}${gap_tracker.target_change:,}")

    # Divider and verdict
    lines.append("━" * 32)

    if gap_tracker.is_catching_up:
        lines.append(f"Catching up by ${gap_tracker.net_position:,}/year")
    else:
        lines.append(f"Falling behind by ${abs(gap_tracker.net_position):,}/year")

    return lines


def format_affordability_section(
    affordability: AffordabilityResult,
    target_name: str,
    config: Optional[dict] = None,
) -> List[str]:
    """Format the affordability gap section."""
    lines = []
    base = affordability.base

    # Target breakdown
    lines.append(f"Target: {target_name} @ {format_currency(base.target_price)}")
    lines.append(f"+ Stamp duty: {format_currency(base.stamp_duty)}")
    lines.append(f"+ Purchase costs: {format_currency(base.purchase_costs)}")
    lines.append(f"= Total needed: {format_currency(base.total_purchase_cost)}")
    lines.append("")

    # Your cash breakdown
    lines.append("Your cash (base case):")
    lines.append(f"- Savings: {format_currency(base.savings_balance)}")
    lines.append(f"- Wollo sale: {format_currency(base.ppor_net_proceeds)}")
    lines.append(f"- Revesby equity: {format_currency(base.ip_usable_equity)}")
    lines.append(f"= Total available: {format_currency(base.total_cash)}")
    lines.append("")

    # Gap
    gap = base.affordability_gap
    if gap <= 0:
        lines.append(f"Gap: {format_currency(gap)} (AFFORDABLE!)")
    else:
        lines.append(f"Gap: {format_currency(gap)}")

        # Time to close
        if affordability.months_to_close_gap:
            years = affordability.months_to_close_gap // 12
            months = affordability.months_to_close_gap % 12
            if years > 0:
                lines.append(f"~{years}y {months}m at ${base.monthly_savings:,}/month savings")
            else:
                lines.append(f"~{affordability.months_to_close_gap} months at ${base.monthly_savings:,}/month savings")

    return lines


def format_monthly_report(
    metrics: Dict[str, MetricResult],
    gap_tracker: GapTrackerResult,
    affordability: AffordabilityResult,
    period: str,
    config: Optional[dict] = None,
) -> str:
    """
    Format the complete report message.

    Args:
        metrics: Dict of segment code → MetricResult
        gap_tracker: Gap tracker result
        affordability: Affordability gap analysis
        period: Period string (e.g., "February 2026")
        config: Optional config for display preferences

    Returns:
        Formatted HTML message for Telegram
    """
    # Determine fortnight
    today = date.today()
    fortnight = "1" if today.day <= 15 else "2"

    lines = [
        f"<b>PropertyTracker Report - {period} (Fortnight {fortnight})</b>",
        "",
    ]

    # Get config preferences
    report_config = config.get('report', {}) if config else {}
    show_proxies = report_config.get('show_proxies', ['revesby_houses', 'wollstonecraft_units'])
    show_targets = report_config.get('show_targets', ['lane_cove_houses', 'chatswood_houses'])
    include_explanations = report_config.get('include_explanations', True)

    # Your Properties section
    lines.append("<b>Your Properties</b>")

    for code in show_proxies:
        if code in metrics:
            lines.append(format_metric_line(metrics[code], include_filter=include_explanations))

    lines.append("")

    # Target Markets section
    lines.append("<b>Target Markets</b>")

    for code in show_targets:
        if code in metrics:
            lines.append(format_metric_line(metrics[code], include_filter=False))

    lines.append("")

    # Gap Tracker section
    lines.append("<b>Gap Tracker</b>")
    lines.extend(format_gap_tracker_section(gap_tracker))
    lines.append("")

    # Affordability Gap section
    lines.append("<b>Affordability Gap</b>")
    target_name = gap_tracker.target_display_name
    lines.extend(format_affordability_section(affordability, target_name, config))
    lines.append("")

    # Verdict
    verdict = _compute_verdict(gap_tracker, affordability)
    lines.append(f"<b>Verdict:</b> {verdict}")

    return "\n".join(lines)


def _compute_verdict(gap_tracker: GapTrackerResult, affordability: AffordabilityResult) -> str:
    """Compute a verdict summary based on gap tracker and affordability."""
    parts = []

    # Gap trend
    if gap_tracker.can_compute:
        if gap_tracker.is_catching_up:
            if gap_tracker.net_position and gap_tracker.net_position > 50000:
                parts.append("Strong progress")
            else:
                parts.append("Gaining ground")
        else:
            if gap_tracker.net_position and abs(gap_tracker.net_position) > 50000:
                parts.append("Gap widening")
            else:
                parts.append("Slight headwind")

    # Affordability status
    if affordability.is_affordable:
        parts.append("target is affordable now")
    elif affordability.months_to_close_gap:
        years = affordability.months_to_close_gap // 12
        if years > 10:
            parts.append("long road ahead")
        elif years > 5:
            parts.append(f"~{years} years to target")
        else:
            parts.append(f"~{years} years to target")

    if parts:
        return ". ".join(parts) + "."
    return "Insufficient data for verdict."


def format_alert(
    alert_type: str,
    message: str,
    severity: str = 'info',
) -> str:
    """Format an alert message."""
    emoji_map = {
        'info': '',
        'warning': '',
        'error': '',
        'success': '',
    }
    emoji = emoji_map.get(severity, '')

    return f"{emoji} <b>{alert_type}</b>\n\n{message}"


def send_monthly_report(
    config: TelegramConfig,
    metrics: Dict[str, MetricResult],
    gap_tracker: GapTrackerResult,
    affordability: AffordabilityResult,
    period: str,
    app_config: Optional[dict] = None,
) -> bool:
    """Send the report via Telegram."""
    message = format_monthly_report(metrics, gap_tracker, affordability, period, app_config)
    return send_message(config, message)


def send_alert(
    config: TelegramConfig,
    alert_type: str,
    message: str,
    severity: str = 'info',
) -> bool:
    """Send an alert via Telegram."""
    formatted = format_alert(alert_type, message, severity)
    return send_message(config, formatted)


def send_ingest_failure_alert(
    config: TelegramConfig,
    error_message: str,
) -> bool:
    """Send alert for data ingest failure."""
    return send_alert(
        config,
        "Data Ingest Failed",
        f"Failed to ingest property sales data:\n\n<code>{error_message}</code>",
        severity='error',
    )


def send_gap_widening_alert(
    config: TelegramConfig,
    current_gap: int,
    previous_gap: int,
    threshold: int = 20000,
) -> bool:
    """Send alert if affordability gap widened significantly."""
    change = current_gap - previous_gap

    if change > threshold:
        return send_alert(
            config,
            "Gap Widening Alert",
            f"Affordability gap increased by {format_currency(change)} this month.\n\n"
            f"Current gap: {format_currency(current_gap)}\n"
            f"Previous gap: {format_currency(previous_gap)}",
            severity='warning',
        )
    return True  # No alert needed
