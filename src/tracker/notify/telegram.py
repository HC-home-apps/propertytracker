# src/tracker/notify/telegram.py
"""Telegram notification integration for PropertyTracker.

Sends monthly alerts and ad-hoc notifications via Telegram bot.
"""

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from tracker.compute.metrics import MetricResult
from tracker.compute.equity import AffordabilityResult, format_currency
from tracker.compute.segments import get_outpacing_pairs, SEGMENTS

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
    label: str,
    metric: MetricResult,
    show_period: bool = True,
) -> str:
    """Format a single metric line for Telegram."""
    if metric.is_suppressed:
        return f"• {label}: ⚠️ {metric.suppression_reason}"

    median = format_currency(metric.median_price) if metric.median_price else "N/A"
    yoy = f"{metric.yoy_pct:+.1f}%" if metric.yoy_pct is not None else "N/A"
    n = metric.sample_size

    period_note = ""
    if show_period and metric.period_type != 'monthly':
        period_note = f" ({metric.period_type})"

    return f"• {label}: {median} ({yoy}, n={n}){period_note}"


def format_outpacing_line(
    proxy_code: str,
    target_code: str,
    outpacing: Dict,
) -> str:
    """Format an outpacing comparison line."""
    proxy_name = SEGMENTS[proxy_code].name
    target_name = SEGMENTS[target_code].name

    pct = outpacing.get('pct_spread')
    dollar = outpacing.get('dollar_spread')

    if pct is None:
        return f"• {proxy_name} vs {target_name}: N/A"

    # Emoji for direction
    emoji = "📈" if pct > 0 else "📉" if pct < 0 else "➡️"

    pct_str = f"{pct:+.1f}%"
    dollar_str = format_currency(dollar) if dollar is not None else "N/A"

    return f"• {proxy_name} vs {target_name}: {emoji} {pct_str} ({dollar_str}/yr)"


def format_monthly_report(
    metrics: Dict[str, MetricResult],
    outpacing: List[Dict],
    affordability: AffordabilityResult,
    period: str,
) -> str:
    """
    Format the complete monthly report message.

    Args:
        metrics: Dict of segment code → MetricResult
        outpacing: List of outpacing comparison dicts
        affordability: Affordability gap analysis
        period: Period string (e.g., "January 2024")

    Returns:
        Formatted HTML message for Telegram
    """
    lines = [
        f"<b>🏠 PropertyTracker Report - {period}</b>",
        "",
        "<b>📊 Market Indices</b>",
    ]

    # Proxy markets (your properties)
    lines.append("")
    lines.append("<u>Your Properties</u>")

    if 'revesby_houses' in metrics:
        lines.append(format_metric_line("Revesby Houses", metrics['revesby_houses']))

    if 'wollstonecraft_units' in metrics:
        lines.append(format_metric_line("Wollstonecraft Units", metrics['wollstonecraft_units']))

    # Target markets
    lines.append("")
    lines.append("<u>Target Markets</u>")

    for code in ['lane_cove_houses', 'lane_cove_units', 'chatswood_houses', 'chatswood_units']:
        if code in metrics:
            name = SEGMENTS[code].name
            lines.append(format_metric_line(name, metrics[code]))

    # Outpacing scoreboard
    lines.append("")
    lines.append("<b>⚡ Outpacing Scoreboard</b>")

    for op in outpacing:
        lines.append(format_outpacing_line(
            op['proxy_segment'],
            op['target_segment'],
            op,
        ))

    # Affordability gap
    lines.append("")
    lines.append("<b>💰 Affordability Gap</b>")

    bear_gap = format_currency(affordability.bear.affordability_gap)
    base_gap = format_currency(affordability.base.affordability_gap)
    bull_gap = format_currency(affordability.bull.affordability_gap)

    lines.append(f"• Gap Range: {bull_gap} to {bear_gap}")
    lines.append(f"• Base Case: {base_gap}")

    if affordability.is_affordable:
        lines.append("• Status: ✅ AFFORDABLE")
    else:
        lines.append(f"• Status: ⏳ ~{affordability.months_to_close_gap} months to close")

    # Verdict
    lines.append("")
    verdict = _compute_verdict(outpacing, affordability)
    lines.append(f"<b>📝 Verdict:</b> {verdict}")

    return "\n".join(lines)


def _compute_verdict(outpacing: List[Dict], affordability: AffordabilityResult) -> str:
    """Compute a one-line verdict summary."""
    # Check outpacing trend
    house_op = next((o for o in outpacing if o['target_segment'] == 'lane_cove_houses'), None)

    if house_op and house_op.get('pct_spread') is not None:
        pct = house_op['pct_spread']
        dollar = house_op.get('dollar_spread', 0) or 0

        if pct > 0:
            trend = "Gap narrowing"
            driver = f"Revesby +{pct:.1f}% outpacing"
        elif pct < 0:
            trend = "Gap widening"
            driver = f"Target +{abs(pct):.1f}% faster"
        else:
            trend = "Gap stable"
            driver = "Markets moving equally"

        if abs(dollar) > 20000:
            dollar_note = f" ({format_currency(abs(dollar))}/yr)"
        else:
            dollar_note = ""

        return f"{trend} - {driver}{dollar_note}"

    return "Insufficient data for verdict"


def format_alert(
    alert_type: str,
    message: str,
    severity: str = 'info',
) -> str:
    """Format an alert message."""
    emoji_map = {
        'info': 'ℹ️',
        'warning': '⚠️',
        'error': '🚨',
        'success': '✅',
    }
    emoji = emoji_map.get(severity, 'ℹ️')

    return f"{emoji} <b>{alert_type}</b>\n\n{message}"


def send_monthly_report(
    config: TelegramConfig,
    metrics: Dict[str, MetricResult],
    outpacing: List[Dict],
    affordability: AffordabilityResult,
    period: str,
) -> bool:
    """Send the monthly report via Telegram."""
    message = format_monthly_report(metrics, outpacing, affordability, period)
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
