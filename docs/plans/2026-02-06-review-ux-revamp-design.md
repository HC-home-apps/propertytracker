# Review UX Revamp — Design Document

**Date:** 2026-02-06
**Status:** Approved

## Problem

The current Telegram review experience has three issues:

1. **UI/UX** — Individual messages per sale are cramped and spammy on mobile. No links to verify properties. Hard to scan.
2. **Reliability** — Domain API is paid and may not be available. Sales are being missed. Enrichment data (zoning, year_built) can be wrong or missing with no indication. Button callbacks expire after 24h.
3. **Accuracy** — Irrelevant properties slip through (knockdown-rebuilds in Revesby, wrong building type/noisy location in Wollstonecraft), but filters stay as-is — manual review handles this.

## Design

### 1. Batched Digest Messages

Replace individual review messages with one digest per segment. All pending sales in a single message with inline keyboard buttons.

**Message format (HTML):**

```html
📋 <b>Revesby Houses</b> — 3 to review

1. <a href="https://www.domain.com.au/...">15 Alliance Ave</a> (556sqm)
   $1,420,000 · R2 · Built 1965

2. <a href="https://www.domain.com.au/...">20 Smith St</a> (580sqm)
   $1,380,000 · R2 · Built 1972

3. <a href="https://www.domain.com.au/...">8 Jones Ave</a> (520sqm)
   $1,350,000 · R3 · Year unknown
```

Each address is a clickable hyperlink to the Domain listing page (or Google search fallback).

**Inline keyboard layout:**

```
Row 1: [1 ✅] [1 ❌]
Row 2: [2 ✅] [2 ❌]
Row 3: [3 ✅] [3 ❌]
Row 4: [All ✅]  [All ❌]
```

- One row of buttons per sale, plus a bulk row at the bottom.
- Max 5 sales per message (Telegram keyboard limits). Overflow goes into a second message.
- Each button callback: `review:{segment}:{sale_id}:{yes|no}`
- Bulk buttons: `review:{segment}:all:{yes|no}`

**After tapping a button:**

The message edits itself:
- The decided sale gets a verdict marker in the text (e.g. `✓` or `✗` after the address)
- That sale's button row is removed
- Once all sales are decided, all buttons are removed

### 2. Property Listing Links (No Domain API Required)

Domain API is paid — we do NOT depend on it. Instead, property URLs come from the Google search ingest (see section 3).

**Link sources (in priority order):**

1. **Domain listing URL** — preferred, extracted from Google search results during ingest
2. **realestate.com.au URL** — if no Domain URL found
3. **Other listing site URL** — allhomes, etc.
4. **Google search fallback** — if no listing URL was captured: `https://www.google.com/search?q={address}+{suburb}+sold`

**Storage:** New column `listing_url TEXT` in `sale_classifications` table.

### 3. Google Search Ingest (Replaces Domain API)

The Domain API (`/v1/salesResults` and `/v1/properties/_suggest`) is paid. We replace it with a two-tier approach:

#### Tier 1: Google Search Scrape (Primary)

A Python module that searches Google for recent sold listings per segment. Results come from multiple real estate sites (domain.com.au, realestate.com.au, allhomes.com.au, etc.) and are deduplicated.

**Search queries per segment:**
- Revesby: `sold Revesby house 2026` (+ optional area/street refinements)
- Wollstonecraft: `sold Wollstonecraft 2 bed 1 bath apartment 2026`

**What we extract from Google search results:**
- Listing URL (domain.com.au, realestate.com.au, etc.)
- Address (from the result title/snippet)
- Price (from the snippet, if shown — e.g. "Sold for $1,420,000")
- Beds/baths/car (from the snippet)
- Source site (domain, realestate, etc.)

**Deduplication:**
- Normalise address from each result using existing `normalise_address()`
- Group results by normalised address
- Prefer domain.com.au URL (best listing pages), fall back to realestate.com.au, then others
- Merge data across sources (e.g. price from one, beds/baths from another)
- Deduplicate against existing `provisional_sales` table to avoid re-ingesting known sales

**Anti-blocking measures:**
- Random delays between requests (2-5 seconds)
- Rotating user agent strings
- Max 4-6 queries per weekly run (2 segments × 2-3 queries each)
- Run only during weekly CI, not frequently

**If Google blocks the request:** Fail gracefully, log warning, continue with VG data only.

#### Tier 2: LLM Agent Fallback (When Snippets Lack Detail)

When Google search snippets don't contain enough data (e.g. price missing, beds/baths unclear), use an LLM agent to:

1. Visit the Domain listing URL extracted from Google
2. Extract structured data: price, beds, baths, car spaces, land size, year built, property description
3. Return as structured JSON for storage

**Implementation:** A simple function that calls an LLM (Claude API) with the listing page content and a prompt to extract property details.

**When to trigger:**
- After Google search ingest, for any sale with missing price or missing beds/baths
- Rate limited: max 10 agent calls per weekly run

### 4. Price Withheld Tracking

Sales with "price withheld" (common in AU real estate) are handled specially:

- Ingest the sale as a provisional sale with `price = NULL` and `status = 'price_withheld'`
- Do NOT send for review (can't judge comparability without price)
- Show in weekly report as: `"5/10 Shirley Rd — Price withheld (awaiting VG)"`
- When VG data arrives (~2 months later), the matcher links them, fills in the price
- The sale then enters the normal review queue with price

### 5. Enrichment Without Domain API

**Year built:**
- Primary: extracted by LLM agent from Domain listing page (when agent fallback is triggered)
- Secondary: NSW Planning Portal may include build year in some council data
- Fallback: show "Year unknown" in review digest

**Zoning:**
- NSW Planning Portal API (free, unchanged)
- When API returns multiple/ambiguous results: display "Zoning unverified"

**Domain listing URL:**
- Captured during Google search ingest (the search result IS a domain.com.au URL)
- No separate API call needed

### 6. Callback Handling

**Cloudflare Worker (`webhook/worker.js`):**

Updated to handle batched message callbacks:
- Parse callback data `review:{segment}:{sale_id_or_all}:{yes|no}`
- For individual: update DB, edit message to mark that sale's verdict, remove that button row
- For "all": update all sales in that message, remove all buttons, mark all verdicts
- Trigger GitHub Actions `repository_dispatch` to persist to DB

**Remove:**
- 6h poll workflow (`review-poll.yml`) — Worker handles callbacks immediately
- Individual review message sending (`send_review_with_buttons` for single sales)

### 7. What Doesn't Change

- Segment filters (area, zoning, year_built thresholds, duplex keywords) — stay as-is
- No rejection reason codes — keep review as simple Yes/No
- Median calculations — still only use `use_in_median=1` sales
- VG ingest pipeline — unchanged
- Address matching/normalisation — unchanged
- Weekly report format — unchanged

## Files to Modify

| File | Change |
|------|--------|
| `src/tracker/notify/telegram.py` | New `send_review_digest()` function; remove single-sale review flow |
| `src/tracker/enrich/pipeline.py` | Store `listing_url`, improve error labelling |
| `src/tracker/enrich/domain.py` | Replace with LLM agent fallback for detail extraction |
| `src/tracker/db.py` | Add `listing_url` column to `sale_classifications`; add `price_withheld` status to `provisional_sales` |
| `src/tracker/cli.py` | Update `review-buttons` command to send digests |
| `src/tracker/ingest/domain_sold.py` | Replace Domain API calls with Google search scrape |
| `src/tracker/ingest/google_search.py` | **New file:** Google search scraper for sold listings |
| `src/tracker/ingest/llm_agent.py` | **New file:** LLM agent fallback for extracting listing details |
| `webhook/worker.js` | Handle batched callbacks, edit message with verdicts |
| `.github/workflows/weekly-report.yml` | Update ingest + review steps |
| `.github/workflows/review-poll.yml` | Remove (replaced by Worker) |

## Implementation Order

1. Google search ingest: new `google_search.py` module, update `domain_sold.py`
2. LLM agent fallback: new `llm_agent.py` module
3. DB schema: add `listing_url` column, `price_withheld` status
4. Price withheld tracking: ingest + deferred review logic
5. Enrichment: use LLM agent for year_built, improve error labelling
6. Telegram digest: new `send_review_digest()` with batched buttons + clickable address links
7. Cloudflare Worker: handle batched callbacks
8. CLI: update `review-buttons` to send digests
9. Cleanup: remove Domain API dependency, remove old single-sale review flow, remove poll workflow
