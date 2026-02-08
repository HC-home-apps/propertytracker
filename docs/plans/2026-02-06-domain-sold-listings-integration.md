# Domain API Sold Listings Integration

**Date:** 2026-02-06
**Status:** Approved

## Problem

NSW Valuer General data has a ~2 month lag. The most recent Wollstonecraft sale in the Feb 1, 2026 dataset has a contract date of Dec 6, 2025. Sales like 9/27-29 Morton St (exchanged Feb 3, 2026) won't appear until ~April 2026.

## Goal

Add Domain API as a secondary, provisional data source to:
- **Close the recency gap** — see sales within days instead of months
- **Improve coverage** — catch sales the VG data might miss (off-market, delayed reporting)

## Design Decisions

1. **Domain API only** — no realestate.com.au (no public API, scraping is fragile/ToS risk)
2. **VG-only medians** — Domain sales shown for visibility but never included in median calculations
3. **Separate table** — `provisional_sales` keeps provisional data isolated from trusted `raw_sales`
4. **Address-based dedup** — normalised address + date window matching links Domain records to VG records

## Data Flow

```
Domain API (weekly)                NSW VG (weekly, ~2mo lag)
  sold listings search               archive.zip download
  per suburb/segment                  CSV parse
        |                                  |
   provisional_sales table           raw_sales table
   (source='domain', unconfirmed)    (source='vg', confirmed)
        |                                  |
   Address normalisation <--- shared normalise.py ---> Address normalisation
        |                                  |
        +-------- Match on normalised address + date window (+-14 days) ------+
                                    |
                  If matched: link records, mark provisional as "confirmed"
                  If unmatched VG: insert as normal
                  If unmatched Domain: stays provisional
```

## Workflow Integration

```
Step 1:  Ingest VG data (existing, unchanged)
Step 2:  NEW - Fetch Domain sold listings per segment
Step 3:  NEW - Run address matching to link provisional -> VG
Step 4:  Enrich (existing, unchanged - only enriches VG records)
Step 5:  Compute metrics (existing, unchanged - only uses raw_sales)
Step 6:  Send report (updated - includes "Recent Unconfirmed Sales" section)
```

## Database Schema

### New table: `provisional_sales`

```sql
CREATE TABLE provisional_sales (
    id TEXT PRIMARY KEY,              -- domain listing ID
    source TEXT NOT NULL,             -- 'domain' (extensible later)
    unit_number TEXT,
    house_number TEXT,
    street_name TEXT,
    suburb TEXT NOT NULL,
    postcode TEXT,
    property_type TEXT,               -- 'house', 'unit'
    sold_price INTEGER,
    sold_date DATE,                   -- date reported as sold
    address_normalised TEXT,          -- output of normalise.py for matching
    matched_dealing_number TEXT,      -- NULL until VG confirms, then FK to raw_sales
    status TEXT DEFAULT 'unconfirmed', -- 'unconfirmed' | 'confirmed' | 'superseded'
    raw_json TEXT,                    -- full API response for debugging
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Status values:
- **unconfirmed**: Domain says sold, VG hasn't confirmed yet
- **confirmed**: Matched to a VG record (linked via matched_dealing_number)
- **superseded**: VG record arrived, Domain record no longer needed for display

### Existing tables: unchanged

`raw_sales`, `monthly_metrics`, `sale_classifications` etc. are not modified.

## Domain API Integration

### Endpoint

`POST /v2/salesResults/listings` — accepts suburb + property type filters, returns recent sold listings.

Called once per segment per week, right after VG ingest.

### Authentication

Uses existing `DOMAIN_API_KEY` environment variable. OAuth 2.0 client credentials flow.

### Rate Limiting

1-second delay between requests (same as existing enrichment pattern).

## Address Matching (Deduplication)

```python
for each unconfirmed provisional_sale:
    normalised = normalise_address(unit, house, street, suburb)

    candidates = raw_sales WHERE:
        suburb = same suburb (exact)
        AND contract_date BETWEEN sold_date - 14 days AND sold_date + 14 days
        AND property_type = same type

    for candidate in candidates:
        candidate_normalised = normalise_address(candidate fields)
        if candidate_normalised == normalised:
            link them (set matched_dealing_number, status='confirmed')
            break
```

Reuses existing `normalise.py` (street type mapping, address parsing). The +-14 day window accounts for Domain reporting auction/exchange date vs VG recording contract date.

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Domain API key missing | Skip Domain ingest, log warning, continue with VG only |
| Domain API rate limited | 1-second delay between requests |
| Domain returns no results | Normal - not every suburb has recent sales every week |
| Multiple VG records match one Domain sale | Match on closest date; if tied, skip and leave unconfirmed |
| Domain sale never gets VG match | Stays unconfirmed indefinitely - fine for display |
| Price differs between Domain and VG | Irrelevant - VG price for medians, Domain price for provisional display only |

## Report Changes

New section in Telegram report:

```
Recent Unconfirmed Sales (Domain)
  - 9/27-29 Morton St, Wollstonecraft - $XXXk (3 Feb)
  - ...
  (Not included in medians - awaiting VG confirmation)
```

## What Doesn't Change

- `raw_sales` table structure
- Median calculations (only query raw_sales)
- Enrichment pipeline (only enriches VG records)
- Existing tests

## Implementation Plan

### Phase 1: Domain API client
- New module `src/tracker/ingest/domain_sold.py`
- OAuth token management
- Sold listings search per suburb
- Parse response into provisional_sales format
- Tests with mocked API responses

### Phase 2: Database & ingest
- Add provisional_sales table to schema
- Insert/upsert logic with dedup on listing ID
- CLI command: `tracker ingest-domain`
- Tests for DB operations

### Phase 3: Address matching
- Matcher module using normalise.py
- Link provisional -> raw_sales on match
- CLI command: `tracker match-provisional`
- Tests with various address format edge cases

### Phase 4: Report integration
- Update Telegram report to include unconfirmed sales section
- Only show unconfirmed (not yet matched to VG)
- Clear labelling that these are provisional

### Phase 5: Workflow integration
- Add steps to weekly-report.yml
- Wire up DOMAIN_API_KEY secret (already exists)
