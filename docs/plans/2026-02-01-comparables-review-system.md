# Comparables Review System Design

## Summary

Add a buyer's-agent-level comparables system for Revesby that:
- Auto-enriches sales with zoning (NSW Planning Portal) and year built (Domain API)
- Auto-excludes non-R2 zoning, modern builds (>2010), and existing duplexes
- Sends remaining sales to Telegram for manual review via reply codes
- Only uses manually-approved sales for median calculations

## Problem

Current system filters Revesby by suburb + land size (500-600sqm), but this includes:
- Modern duplexes (different product, higher price)
- Non-R2 zoned properties (can't build duplex)
- Renovated homes (not comparable to original fibro)

The IP's value is primarily as a **duplex site** - comparables must match this profile.

## Scope

| Segment | Manual Review | Reasoning |
|---------|--------------|-----------|
| Revesby Houses (IP) | Yes | Duplex site value - comparables must match |
| Wollstonecraft Units | No | Units homogeneous, street filter sufficient |
| Lane Cove Houses | No | Market tracking, not precise valuation |
| Chatswood Houses | No | Market tracking, not precise valuation |

---

## Data Model

### New table: `sale_classifications`

```sql
CREATE TABLE sale_classifications (
    sale_id TEXT PRIMARY KEY,           -- Links to raw_sales
    address TEXT NOT NULL,

    -- Auto-enriched fields
    zoning TEXT,                        -- R2, R3, B1, etc.
    year_built INTEGER,                 -- From Domain lookup
    has_duplex_keywords BOOLEAN,        -- "duplex", "dual occ" in description

    -- Computed flags
    is_auto_excluded BOOLEAN,           -- TRUE if fails auto-filter
    auto_exclude_reason TEXT,           -- "non-R2 zoning" / "modern (2018)" / "duplex"

    -- Manual review
    review_status TEXT DEFAULT 'pending',  -- pending | comparable | not_comparable
    reviewed_at TIMESTAMP,
    review_notes TEXT,

    -- For metrics
    use_in_median BOOLEAN DEFAULT FALSE  -- Only TRUE if approved
);
```

### Sale flow

```
New Sale Ingested
      │
      ▼
┌─────────────────────────┐
│ Auto-Enrichment         │
│ - Zoning (NSW Portal)   │
│ - Year built (Domain)   │
│ - Keyword scan          │
└─────────────────────────┘
      │
      ▼
┌─────────────────────────┐
│ Auto-Exclude Check      │
│                         │
│ IF zoning NOT IN (R2)   │
│   → EXCLUDE             │
│                         │
│ IF year_built > 2010    │
│   → EXCLUDE             │
│                         │
│ IF duplex keywords      │
│   → EXCLUDE             │
└─────────────────────────┘
      │
      ▼
   Excluded? ──Yes──► is_auto_excluded = TRUE
      │                    (never sent for review)
      No
      │
      ▼
   review_status = 'pending'
   Sent to Telegram for review
```

---

## Auto-Enrichment Pipeline

### 1. Zoning Lookup

**Source:** NSW Planning Portal API (free, public)

```python
def get_zoning(address: str) -> Optional[str]:
    """
    Query NSW Planning Portal for property zoning.
    Returns: R2, R3, B1, etc. or None if not found
    """
    # Rate limit: 1 req/sec with retry backoff
```

### 2. Year Built Lookup

**Source:** Domain Property API

```python
def get_year_built(address: str, suburb: str) -> Optional[int]:
    """
    Query Domain API for property year built.
    Returns: Year (e.g., 1965) or None if not found

    API key required (free tier: 500 calls/month)
    Fallback: scrape if no API key
    """
```

### 3. Keyword Scan

**Check NSW description field for:**
- "duplex"
- "dual occ"
- "torrens"
- "brand new"
- "just completed"

```python
EXCLUDE_KEYWORDS = ['duplex', 'dual occ', 'torrens', 'brand new', 'just completed']

def has_exclude_keywords(description: str) -> bool:
    return any(kw in description.lower() for kw in EXCLUDE_KEYWORDS)
```

### 4. Auto-Exclude Decision

```python
def should_auto_exclude(zoning: str, year_built: int, has_keywords: bool) -> tuple[bool, str]:
    """
    Returns (is_excluded, reason)
    """
    if zoning and zoning not in ('R2', 'R3'):
        return True, f"non-R2 zoning ({zoning})"

    if year_built and year_built > 2010:
        return True, f"modern build ({year_built})"

    if has_keywords:
        return True, "existing duplex"

    return False, None
```

---

## Telegram Review Flow

### Review message format

Sent with fortnightly report when there are pending sales:

```
📋 3 Revesby sales need review:

1. 15 Smith St - $1,420,000 (556sqm)
   🏷️ R2 | Built 1965 | Original fibro
   🔗 domain.com.au/15-smith-st-revesby

2. 8 Jones Ave - $1,380,000 (520sqm)
   🏷️ R2 | Built 1972 | Original brick
   🔗 domain.com.au/8-jones-ave-revesby

3. 22 Brown Rd - $1,510,000 (580sqm)
   🏷️ R2 | Built 1968 | Original fibro
   🔗 domain.com.au/22-brown-rd-revesby

Reply: 1✅ 2✅ 3❌
(or just ✅✅❌)
```

### Reply parsing

```python
def parse_review_reply(text: str, sale_count: int) -> list[str]:
    """
    Parse user reply into list of statuses.

    Accepts:
    - "✅✅❌" - shorthand in order
    - "1✅ 2✅ 3❌" - explicit numbering
    - "all✅" - mark all comparable
    - "skip" - keep pending

    Returns: ['comparable', 'comparable', 'not_comparable']
    """
```

### After reply

1. Update `sale_classifications.review_status`
2. Set `use_in_median = TRUE` for approved sales
3. Recalculate median using only approved sales
4. Next report shows: "Median based on 12 verified comparables"

### Fallback if no reply

- Pending sales stay pending
- Report shows: "3 sales awaiting review (median may be inaccurate)"
- Reminder sent after 7 days

---

## Config Changes

```yaml
# config.yml additions

enrichment:
  domain_api_key: ${DOMAIN_API_KEY}  # From environment

segments:
  revesby_houses:
    display_name: "Revesby Houses (IP)"
    suburbs: [revesby, revesby heights]
    property_type: house
    role: proxy
    filters:
      area_min: 500
      area_max: 600

    # New: Enable review workflow
    require_manual_review: true
    auto_exclude:
      non_r2_zoning: true
      year_built_after: 2010
      keywords: [duplex, dual occ, torrens, brand new, just completed]

  wollstonecraft_units:
    # ... no changes, require_manual_review defaults to false
```

---

## File Changes

### New files

```
src/tracker/
├── enrich/
│   ├── __init__.py
│   ├── zoning.py       # NSW Planning Portal API client
│   ├── domain.py       # Domain API for year built
│   └── classifier.py   # Auto-exclude logic + keyword scan
├── review/
│   ├── __init__.py
│   └── telegram.py     # Review formatting + reply parsing
```

### Modified files

| File | Changes |
|------|---------|
| `db.py` | Add `sale_classifications` table |
| `metrics.py` | Filter to `use_in_median = TRUE` for reviewed segments |
| `telegram.py` | Add review section, handle reply webhooks |
| `config.yml.example` | Add enrichment + auto_exclude config |

---

## Backfill Strategy

### Volume

- Last 2 years Revesby (500-600sqm): **90 sales**
- Estimated auto-excluded (40%): ~36
- Requiring manual review: **~54 sales**

### Process

1. Run enrichment on all existing Revesby sales
2. Auto-exclude based on rules
3. Send remaining sales in batches of 10 for review
4. Total user effort: ~30 minutes one-time

### Ongoing

- ~5-10 new Revesby sales per quarter in filter range
- Auto-exclude removes ~40%
- ~3-6 sales per fortnight for review
- ~2 minutes per fortnight

---

## Implementation Phases

### Phase 1: Data model + auto-enrichment
- Create `sale_classifications` table
- Implement zoning API client
- Implement Domain API client
- Implement keyword scanner
- Wire up auto-exclude logic

### Phase 2: Telegram review flow
- Format review messages
- Parse reply codes
- Update classifications on reply
- Add webhook handler for replies

### Phase 3: Metrics integration
- Modify `get_period_sales()` to filter by `use_in_median`
- Add "verified comparables" count to reports
- Add "pending review" warning to reports

### Phase 4: Backfill
- Run enrichment on existing data
- Send backfill batches for review
- Verify median accuracy

---

## API Considerations

### NSW Planning Portal
- Free, public API
- No authentication required
- Rate limit: Be respectful, 1 req/sec

### Domain API
- Requires API key (free tier: 500 calls/month)
- Plenty for ~10-20 Revesby sales/month
- Fallback: scrape if no API key configured

### Rate Limiting
- All API calls: 1 request/second
- Exponential backoff on errors
- Cache results in `sale_classifications`

---

## Success Criteria

1. Auto-exclude removes obvious non-comparables (duplexes, modern builds)
2. Manual review takes <5 minutes per fortnight
3. Median calculation uses only verified comparable sales
4. Reports clearly show comparable count and pending status
