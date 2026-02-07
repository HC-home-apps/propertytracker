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
    report_chat_id: Optional[str] = None  # Separate chat ID for reports (e.g., group chat)
    report_chat_ids: Optional[str] = None  # Comma-separated report chat IDs

    @classmethod
    def from_env(cls) -> 'TelegramConfig':
        """Load config from environment variables."""
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        report_chat_id = os.getenv('TELEGRAM_REPORT_CHAT_ID')
        report_chat_ids = os.getenv('TELEGRAM_REPORT_CHAT_IDS')

        if not token or not chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set"
            )

        return cls(
            bot_token=token,
            chat_id=chat_id,
            report_chat_id=report_chat_id,
            report_chat_ids=report_chat_ids,
        )

    def get_report_chat_id(self) -> str:
        """Get chat ID for reports (uses report_chat_id if set, else chat_id)."""
        return self.report_chat_id or self.chat_id

    def get_report_chat_ids(self) -> List[str]:
        """Get all report chat IDs (deduplicated, preserving order)."""
        ids: List[str] = []

        if self.report_chat_ids:
            ids.extend(
                part.strip() for part in self.report_chat_ids.split(',')
                if part.strip()
            )

        if self.report_chat_id:
            ids.append(self.report_chat_id)

        if not ids:
            ids.append(self.chat_id)

        # Dedupe while preserving order
        seen = set()
        deduped = []
        for cid in ids:
            if cid not in seen:
                deduped.append(cid)
                seen.add(cid)

        return deduped


def send_message(
    config: TelegramConfig,
    message: str,
    parse_mode: str = 'HTML',
    use_report_chat: bool = False,
) -> bool:
    """
    Send a message via Telegram bot.

    Args:
        config: Telegram configuration
        message: Message text (HTML or plain)
        parse_mode: 'HTML' or 'MarkdownV2'
        use_report_chat: If True, send to report_chat_id (for shared reports)

    Returns:
        True if successful, False otherwise
    """
    url = f"{TELEGRAM_API_BASE}{config.bot_token}/sendMessage"

    payload = {
        'text': message,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True,
    }

    # Regular messages: single destination (personal chat).
    if not use_report_chat:
        request_payload = dict(payload)
        request_payload['chat_id'] = config.chat_id
        try:
            response = requests.post(url, json=request_payload, timeout=30)
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    # Report messages: can target one or many report chats.
    target_chat_ids = config.get_report_chat_ids()
    success = False

    for chat_id in target_chat_ids:
        request_payload = dict(payload)
        request_payload['chat_id'] = chat_id
        try:
            response = requests.post(url, json=request_payload, timeout=30)
            if response.status_code != 200:
                logger.error(f"Telegram API error {response.status_code} for chat {chat_id}: {response.text}")
                continue
            success = True
        except requests.RequestException as e:
            logger.error(f"Failed to send Telegram message to chat {chat_id}: {e}")

    # Fallback: if all configured report chats failed, try personal chat.
    if not success and config.chat_id not in target_chat_ids:
        logger.info("Falling back to personal chat for report delivery")
        request_payload = dict(payload)
        request_payload['chat_id'] = config.chat_id
        try:
            fallback = requests.post(url, json=request_payload, timeout=30)
            if fallback.status_code == 200:
                return True
            logger.error(f"Fallback also failed: {fallback.status_code}: {fallback.text}")
        except requests.RequestException as e:
            logger.error(f"Fallback to personal chat failed: {e}")

    return success


def send_review_with_buttons(
    config: TelegramConfig,
    sale_id: str,
    address: str,
    price: int,
    area_sqm: Optional[float],
    zoning: Optional[str],
    year_built: Optional[int],
    segment_code: str,
) -> bool:
    """
    Send a review request with inline Yes/No buttons.

    Args:
        config: Telegram configuration
        sale_id: Unique sale identifier (dealing_number)
        address: Property address
        price: Sale price
        area_sqm: Land area in sqm
        zoning: Zoning code
        year_built: Year property was built
        segment_code: Segment being reviewed

    Returns:
        True if successful
    """
    from tracker.compute.equity import format_currency

    url = f"{TELEGRAM_API_BASE}{config.bot_token}/sendMessage"

    # Format message
    area_str = f"{area_sqm:.0f}sqm" if area_sqm else "N/A"
    zoning_str = zoning or "Unknown"
    year_str = f"Built {year_built}" if year_built else "Year unknown"

    message = (
        f"<b>{address}</b>\n"
        f"{format_currency(price)} | {area_str}\n"
        f"{zoning_str} | {year_str}\n\n"
        f"Is this comparable to your property?"
    )

    # Inline keyboard with Yes/No buttons
    # callback_data format: "review:SEGMENT:SALE_ID:yes" or "review:SEGMENT:SALE_ID:no"
    keyboard = {
        "inline_keyboard": [[
            {"text": "Yes", "callback_data": f"review:{segment_code}:{sale_id}:yes"},
            {"text": "No", "callback_data": f"review:{segment_code}:{sale_id}:no"},
        ]]
    }

    payload = {
        'chat_id': config.chat_id,  # Reviews go to personal chat, not group
        'text': message,
        'parse_mode': 'HTML',
        'reply_markup': keyboard,
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send review message: {e}")
        return False


def format_review_digest(segment_name: str, sales: List[dict]) -> str:
    """
    Format a review digest message with clickable address links.

    Args:
        segment_name: Human-readable segment name (e.g., "Revesby Houses")
        sales: List of sale dicts with keys: sale_id, address, price, area_sqm,
               zoning_label, year_built_label, listing_url

    Returns:
        HTML-formatted digest message
    """
    from tracker.compute.equity import format_currency
    from urllib.parse import quote_plus

    count = len(sales)
    lines = [f"📋 <b>{segment_name}</b> — {count} to review\n"]

    for idx, sale in enumerate(sales, 1):
        address = sale['address']
        price = sale['price']
        area_sqm = sale.get('area_sqm')
        zoning_label = sale.get('zoning_label', 'Unknown')
        year_built_label = sale.get('year_built_label', 'Year unknown')
        listing_url = sale.get('listing_url')

        # Use listing_url if available, else construct Google search fallback
        if not listing_url:
            search_query = quote_plus(f"{address} sold")
            listing_url = f"https://www.google.com/search?q={search_query}"

        # Format area
        area_str = f" ({area_sqm:.0f}sqm)" if area_sqm else ""

        # Build line with clickable address link
        line = (
            f"{idx}. <a href=\"{listing_url}\">{address}</a>{area_str}\n"
            f"   {format_currency(price)} · {zoning_label} · {year_built_label}"
        )
        lines.append(line)

    return "\n\n".join(lines)


def build_digest_keyboard(sale_ids: List[tuple], segment_code: str) -> dict:
    """
    Build inline keyboard for digest review with individual + bulk buttons.

    Args:
        sale_ids: List of (sale_id, segment_code) tuples (max 5)
        segment_code: Segment code

    Returns:
        Telegram inline keyboard dict with one row per sale + bulk row
    """
    rows = []

    # Abbreviate segment code to fit Telegram's 64-byte callback_data limit
    # (e.g. "wollstonecraft_units" → "woll" saves 16 bytes)
    seg_short = segment_code[:4]

    # One row per sale
    for idx, (sale_id, _) in enumerate(sale_ids, 1):
        rows.append([
            {"text": f"{idx} ✅", "callback_data": f"review:{seg_short}:{sale_id}:yes"},
            {"text": f"{idx} ❌", "callback_data": f"review:{seg_short}:{sale_id}:no"},
        ])

    # Bulk row
    rows.append([
        {"text": "All ✅", "callback_data": f"review:{seg_short}:all:yes"},
        {"text": "All ❌", "callback_data": f"review:{seg_short}:all:no"},
    ])

    return {"inline_keyboard": rows}


def send_review_digest(
    config: TelegramConfig,
    segment_name: str,
    sales: List[dict],
    segment_code: str,
) -> bool:
    """
    Send a batched review digest with inline keyboard.

    Args:
        config: Telegram configuration
        segment_name: Human-readable segment name
        sales: List of sales (max 5 per message)
        segment_code: Segment code

    Returns:
        True if successful
    """
    if not sales:
        return False

    url = f"{TELEGRAM_API_BASE}{config.bot_token}/sendMessage"

    # Format message
    message = format_review_digest(segment_name, sales)

    # Build keyboard
    sale_ids = [(s['sale_id'], segment_code) for s in sales]
    keyboard = build_digest_keyboard(sale_ids, segment_code)

    payload = {
        'chat_id': config.chat_id,  # Reviews go to personal chat
        'text': message,
        'parse_mode': 'HTML',
        'reply_markup': keyboard,
        'disable_web_page_preview': True,
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send review digest: {e}")
        return False


def get_callback_updates(config: TelegramConfig, offset: Optional[int] = None) -> List[dict]:
    """
    Poll for callback query updates from button presses.

    Args:
        config: Telegram configuration
        offset: Update offset (to avoid getting same updates again)

    Returns:
        List of callback query updates
    """
    url = f"{TELEGRAM_API_BASE}{config.bot_token}/getUpdates"

    params = {
        'allowed_updates': ['callback_query'],
        'timeout': 5,
    }
    if offset is not None:
        params['offset'] = offset

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data.get('ok'):
            return data.get('result', [])
        return []
    except requests.RequestException as e:
        logger.error(f"Failed to get updates: {e}")
        return []


def answer_callback_query(config: TelegramConfig, callback_query_id: str, text: str = "") -> bool:
    """
    Acknowledge a callback query (required by Telegram).

    Args:
        config: Telegram configuration
        callback_query_id: ID of the callback query to answer
        text: Optional notification text to show user

    Returns:
        True if successful
    """
    url = f"{TELEGRAM_API_BASE}{config.bot_token}/answerCallbackQuery"

    payload = {
        'callback_query_id': callback_query_id,
        'text': text,
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to answer callback: {e}")
        return False


def edit_message_remove_buttons(
    config: TelegramConfig,
    chat_id: int,
    message_id: int,
    new_text: Optional[str] = None,
) -> bool:
    """
    Remove inline keyboard buttons from a message and optionally update text.

    Args:
        config: Telegram configuration
        chat_id: Chat containing the message
        message_id: Message to edit
        new_text: If provided, replaces the message text

    Returns:
        True if successful
    """
    if new_text:
        url = f"{TELEGRAM_API_BASE}{config.bot_token}/editMessageText"
        payload = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': new_text,
            'parse_mode': 'HTML',
        }
    else:
        url = f"{TELEGRAM_API_BASE}{config.bot_token}/editMessageReplyMarkup"
        payload = {
            'chat_id': chat_id,
            'message_id': message_id,
            'reply_markup': {'inline_keyboard': []},
        }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to edit message: {e}")
        return False


def delete_message(config: TelegramConfig, chat_id: int, message_id: int) -> bool:
    """Delete a Telegram message entirely."""
    url = f"{TELEGRAM_API_BASE}{config.bot_token}/deleteMessage"
    payload = {'chat_id': chat_id, 'message_id': message_id}
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to delete message: {e}")
        return False


def format_metric_line(
    metric: MetricResult,
    include_filter: bool = True,
    include_dates: bool = True,
) -> str:
    """
    Format a single metric line for Telegram.

    Args:
        metric: MetricResult to format
        include_filter: Whether to include filter description
        include_dates: Whether to include date range

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

    # Add date range for transparency
    if include_dates and metric.oldest_sale_date and metric.newest_sale_date:
        line += f"\n  Sales from: {metric.oldest_sale_date} to {metric.newest_sale_date}"

    return line


def format_recent_sales(metric: MetricResult, max_sales: int = 5) -> List[str]:
    """Format recent sales for transparency."""
    lines = []
    if not metric.recent_sales:
        return lines

    lines.append(f"<u>Last {len(metric.recent_sales)} sales ({metric.display_name or metric.segment}):</u>")
    for sale in metric.recent_sales[:max_sales]:
        area_str = f" ({sale.area_sqm:.0f}sqm)" if sale.area_sqm else ""
        lines.append(f"  {sale.contract_date}: {sale.address} - {format_currency(sale.price)}{area_str}")

    return lines


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

    # Add transparency section with recent sales (optional, controlled by config)
    show_recent_sales = report_config.get('show_recent_sales', False)
    if show_recent_sales:
        lines.append("")
        lines.append("<b>Recent Sales (for transparency)</b>")
        for code in show_proxies:
            if code in metrics and metrics[code].recent_sales:
                lines.extend(format_recent_sales(metrics[code], max_sales=3))

    return "\n".join(lines)


def format_detailed_report(
    metrics: Dict[str, MetricResult],
    gap_tracker: GapTrackerResult,
    affordability: AffordabilityResult,
    period: str,
    config: Optional[dict] = None,
) -> str:
    """
    Format a detailed report with all transparency information.

    This is a longer format that includes:
    - All sections from the standard report
    - Recent sales for each segment
    - Calculation rules explanation
    """
    # Start with standard report
    report = format_monthly_report(metrics, gap_tracker, affordability, period, config)

    lines = [report, "", "<b>Sample Details</b>"]

    # Show recent sales for all segments
    report_config = config.get('report', {}) if config else {}
    show_proxies = report_config.get('show_proxies', ['revesby_houses', 'wollstonecraft_units'])

    for code in show_proxies:
        if code in metrics:
            metric = metrics[code]
            if metric.recent_sales:
                lines.append("")
                lines.extend(format_recent_sales(metric, max_sales=5))

    # Add calculation rules
    lines.append("")
    lines.append("<b>Calculation Rules</b>")
    lines.append("• Medians use sales from last 6 months (or fallback periods)")
    lines.append("• YoY compares same period vs prior year")
    lines.append("• Filters: area (sqm) and/or streets as configured")
    lines.append("• Equity: (Value × 0.95 haircut × 0.80 LVR) - Debt")
    lines.append("• PPOR net: (Value × 0.95 - 2% costs) - Debt")

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
    """Send the report via Telegram (to report chat if configured)."""
    message = format_monthly_report(metrics, gap_tracker, affordability, period, app_config)
    return send_message(config, message, use_report_chat=True)


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


# ============================================================
# SIMPLIFIED REPORT FORMAT
# ============================================================

@dataclass
class SegmentPosition:
    """Position summary for a single segment."""
    segment_code: str
    display_name: str
    median_price: Optional[int]
    debt: int
    equity_or_net: Optional[int]  # Usable equity (IP) or net proceeds (PPOR)
    is_ppor: bool  # True = PPOR (net after sale), False = IP (usable equity)
    target_80_lvr: Optional[int] = None  # Target value for 80% LVR (IP only)


def compute_segment_position(
    metric: MetricResult,
    debt: int,
    is_ppor: bool,
    haircut: float = 0.95,
    lvr_cap: float = 0.80,
    selling_cost_rate: float = 0.02,
) -> SegmentPosition:
    """
    Compute position for a single segment.

    For PPOR: net = (value * haircut - selling_costs) - debt
    For IP: equity = (value * haircut * lvr_cap) - debt
         target_80_lvr = debt / lvr_cap (property value needed for 80% LVR)
    """
    equity_or_net = None
    target_80_lvr = None

    if metric.median_price:
        if is_ppor:
            # PPOR: Net proceeds after sale
            gross = metric.median_price * haircut
            selling_costs = metric.median_price * selling_cost_rate
            equity_or_net = int(gross - selling_costs - debt)
        else:
            # IP: Usable equity at 80% LVR
            gross = metric.median_price * haircut * lvr_cap
            equity_or_net = int(gross - debt)
            # Target value needed for 80% LVR (when usable equity becomes positive)
            target_80_lvr = int(debt / lvr_cap)

    return SegmentPosition(
        segment_code=metric.segment,
        display_name=metric.display_name or metric.segment,
        median_price=metric.median_price,
        debt=debt,
        equity_or_net=equity_or_net,
        is_ppor=is_ppor,
        target_80_lvr=target_80_lvr,
    )


def _format_provisional_address(sale: dict) -> str:
    """Format address from provisional sale dict."""
    unit = sale.get('unit_number', '')
    house = sale.get('house_number', '')
    street = sale.get('street_name', '')
    suburb = sale.get('suburb', '')

    addr_parts = []
    if unit:
        addr_parts.append(f"{unit}/")
    if house:
        addr_parts.append(f"{house} ")
    addr_parts.append(f"{street}, {suburb.title()}")
    return "".join(addr_parts)


def _format_sold_date(date_str: str) -> str:
    """Format ISO date string (2026-01-30) to human-readable (30 Jan 2026)."""
    if not date_str:
        return 'Recent'
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
        return dt.strftime('%-d %b %Y')
    except (ValueError, TypeError):
        return date_str


def _format_bed_bath_car(sale: dict) -> str:
    """Format bedroom/bath/car info if available."""
    parts = []
    bed = sale.get('bedrooms')
    bath = sale.get('bathrooms')
    car = sale.get('car_spaces')
    if bed is not None:
        parts.append(f"{bed}bed")
    if bath is not None:
        parts.append(f"{bath}bath")
    if car is not None:
        parts.append(f"{car}car")
    return "/".join(parts)


def format_simple_report(
    new_sales: Dict[str, List],  # segment_code -> list of SaleRecord
    positions: Dict[str, SegmentPosition],  # segment_code -> SegmentPosition
    period: str,
    config: Optional[dict] = None,
    provisional_sales: Optional[List[dict]] = None,
    provisional_by_segment: Optional[Dict[str, List[dict]]] = None,
) -> str:
    """
    Format a simplified weekly report.

    Structure:
    1. Recent comparable sales (since last report)
    2. Brief position summary (median -> equity)

    Args:
        new_sales: Dict mapping segment codes to lists of new SaleRecord
        positions: Dict mapping segment codes to SegmentPosition
        period: Period string (e.g., "Feb 2, 2026")
        config: Optional config dict

    Returns:
        Formatted HTML message for Telegram
    """
    lines = [f"<b>PropertyTracker - {period}</b>", ""]

    # Get display order from config
    report_config = config.get('report', {}) if config else {}
    show_proxies = report_config.get('show_proxies', ['revesby_houses', 'wollstonecraft_units'])
    show_targets = report_config.get('show_targets', ['lane_cove_houses', 'chatswood_houses'])

    # Section 1: Recent Sales — all segments, confirmed + unconfirmed inline
    all_segments = show_proxies + show_targets
    for segment_code in all_segments:
        sales = new_sales.get(segment_code, [])

        segment = get_segment(segment_code)
        if not segment:
            continue

        # Skip target segments with no sales
        if segment_code in show_targets and not sales:
            continue

        filter_desc = segment.get_filter_description() or ""
        confirmed_count = sum(1 for s in sales if s.source == 'confirmed')
        unconfirmed_count = sum(1 for s in sales if s.source == 'unconfirmed')
        total = len(sales)

        # Header with filter info and counts
        header = f"<b>{segment.display_name}</b>"
        if filter_desc:
            header += f" ({filter_desc})"
        if unconfirmed_count > 0 and confirmed_count > 0:
            header += f" - {total} new ({unconfirmed_count} unconfirmed)"
        elif unconfirmed_count > 0:
            header += f" - {total} new (unconfirmed)"
        else:
            header += f" - {total} new"
        lines.append(header)

        if sales:
            for sale in sales:
                date_str = _format_sold_date(sale.contract_date) if sale.source == 'unconfirmed' else sale.contract_date
                price_str = format_currency(sale.price)

                # Build address display — link to Domain for unconfirmed
                if sale.source == 'unconfirmed' and sale.listing_url:
                    addr_display = f'<a href="{sale.listing_url}">{sale.address}</a>'
                else:
                    addr_display = sale.address

                # Build line
                if segment.property_type == 'house' and sale.area_sqm:
                    line = f"  {date_str}: {addr_display} ({sale.area_sqm:.0f}sqm) - {price_str}"
                elif sale.bed_bath_car:
                    line = f"  {date_str}: {addr_display} - {price_str} ({sale.bed_bath_car})"
                else:
                    line = f"  {date_str}: {addr_display} - {price_str}"

                if sale.source == 'unconfirmed':
                    line += " <i>(unconfirmed)</i>"

                lines.append(line)
        else:
            lines.append("  No new sales this week")

        lines.append("")

    # Divider
    lines.append("---")

    # Section 2: Position Summary
    for segment_code in show_proxies:
        if segment_code not in positions:
            continue

        pos = positions[segment_code]
        if pos.median_price is None:
            lines.append(f"{pos.display_name}: No data")
            continue

        median_str = format_currency(pos.median_price)
        if pos.equity_or_net is not None:
            equity_str = format_currency(pos.equity_or_net)
            label = "net" if pos.is_ppor else "usable equity"
            # Shorten display name for position line
            short_name = pos.display_name.split(' (')[0]
            line = f"{short_name}: {median_str} median -> ~{equity_str} {label}"
            # Show 80% LVR target for IP when usable equity is negative
            if not pos.is_ppor and pos.equity_or_net < 0 and pos.target_80_lvr:
                target_str = format_currency(pos.target_80_lvr)
                line += f" (target: {target_str} for 80% LVR)"
            lines.append(line)
        else:
            lines.append(f"{pos.display_name}: {median_str} median")

    return "\n".join(lines)


def send_simple_report(
    config: TelegramConfig,
    new_sales: Dict[str, List],
    positions: Dict[str, SegmentPosition],
    period: str,
    app_config: Optional[dict] = None,
    provisional_sales: Optional[List[dict]] = None,
    provisional_by_segment: Optional[Dict[str, List[dict]]] = None,
) -> bool:
    """Send the simplified report via Telegram (to report chat if configured)."""
    message = format_simple_report(
        new_sales, positions, period, app_config,
        provisional_sales=provisional_sales,
        provisional_by_segment=provisional_by_segment,
    )
    return send_message(config, message, use_report_chat=True)
