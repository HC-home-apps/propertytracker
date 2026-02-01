# Simplified Telegram Report Design

**Date:** 2026-02-02
**Status:** Ready for implementation

## Overview

Redesign the weekly Telegram report to focus on what matters: recent comparable sales and current position. Remove the overwhelming analysis sections.

## Report Structure

### Section 1: Recent Comparable Sales (Primary)

Show sales that appeared since the last weekly report. Format: date, address, size/price info.

```
Revesby Houses (500-600sqm) - 3 new
• Oct 25: 24 Alliance Ave (556sqm) - $1,720,000
• Oct 20: 15 Doyle Rd (510sqm) - $1,650,000
• Oct 18: 8 Marco Ave (580sqm) - $1,580,000

Wollstonecraft Units ($1.0-1.5M) - 1 new
• Oct 22: Unit 10/66 Shirley Rd - $1,285,000
```

**If no new sales:** Show "No new sales this week" for that segment.

### Section 2: Your Position (Brief)

Two-line summary showing median and what it means for your equity.

```
Revesby: $1.65M median → ~$165K equity
Wollo: $1.25M median → ~$275K net
```

**Calculation notes:**
- Revesby equity: `(median × 0.95 haircut × 0.80 LVR) - debt`
- Wollo net: `(median × 0.95 haircut - 2% selling costs) - debt`
- Debt figures manually updated in config.yml

### What's Removed

- Gap Tracker section (your assets vs target growth)
- Affordability Gap section (full cash breakdown, stamp duty math)
- Verdict text
- Target market sections (Lane Cove, Chatswood)

## Backend Behavior

### Sales Window
- "New sales" = sales with contract_date since last report was generated
- Track last report date in database or use 7-day window as fallback

### Median Calculation
- Rolling 6-month window of all sales matching segment filters
- Falls back to 3-month if insufficient sample
- New sales contribute to median calculation

### Manual Review Workflow
Both segments have `require_manual_review: true`:

1. **Revesby houses:** Enriched via Domain API (zoning, year built), then sent for y/n review
2. **Wollstonecraft units:** Price-filtered ($1.0M-$1.5M), then sent for y/n review

Only "comparable" tagged sales used in time-adjusted median for verified calculations.

### Debt Updates
- Manual update in `config.yml` (or GitHub secrets for Actions)
- User updates when checking loan statements (~quarterly)
- Property value swings (~$50K/year) dwarf principal paydown (~$20K/year)

## Implementation Changes

### New Report Formatter
Create `format_simple_report()` in `telegram.py` that:
1. Queries sales since last report date for each proxy segment
2. Formats as date/address/size/price list
3. Adds position summary with equity calculation

### Config Addition
```yaml
report:
  format: simple  # 'simple' (new) or 'detailed' (current)
  sales_window: since_last_report  # or 'last_30_days'
```

### Files to Modify
- `src/tracker/notify/telegram.py` - Add simple report formatter
- `src/tracker/cli.py` - Update notify command to use new format
- `config.yml` - Add report format option

## Example Output

```
PropertyTracker - Feb 2, 2026

Revesby Houses (500-600sqm) - 2 new
• Jan 28: 12 Doyle Rd (520sqm) - $1,680,000
• Jan 25: 5 Marco Ave (590sqm) - $1,590,000

Wollstonecraft Units ($1.0-1.5M) - 0 new
No new sales this week

---
Revesby: $1.65M median → ~$165K equity
Wollo: $1.25M median → ~$275K net
```
