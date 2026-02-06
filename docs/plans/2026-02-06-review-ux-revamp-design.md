# Review UX Revamp — Design Document

**Date:** 2026-02-06
**Status:** Approved

## Problem

The current Telegram review experience has three issues:

1. **UI/UX** — Individual messages per sale are cramped and spammy on mobile. No links to verify properties. Hard to scan.
2. **Reliability** — Domain API misses sales. Enrichment data (zoning, year_built) can be wrong or missing with no indication. Button callbacks expire after 24h.
3. **Accuracy** — Irrelevant properties slip through (knockdown-rebuilds in Revesby, wrong building type/noisy location in Wollstonecraft), but filters stay as-is — manual review handles this.

## Design

### 1. Batched Digest Messages

Replace individual review messages with one digest per segment. All pending sales in a single message with inline keyboard buttons.

**Message format (HTML):**

```html
📋 <b>Revesby Houses</b> — 3 to review

1. <a href="https://domain.com.au/...">15 Alliance Ave</a> (556sqm)
   $1,420,000 · R2 · Built 1965

2. <a href="https://domain.com.au/...">20 Smith St</a> (580sqm)
   $1,380,000 · R2 · Built 1972

3. <a href="https://domain.com.au/...">8 Jones Ave</a> (520sqm)
   $1,350,000 · R3 · Year unknown
```

**Inline keyboard layout:**

```
Row 1: [1 ✅] [1 ❌]    [2 ✅] [2 ❌]
Row 2: [3 ✅] [3 ❌]
Row 3: [All ✅]  [All ❌]
```

- Max 5 sales per message (Telegram keyboard limits). Overflow goes into a second message.
- Each button callback: `review:{segment}:{sale_id}:{yes|no}`
- Bulk buttons: `review:{segment}:all:{yes|no}`

**After tapping a button:**

The message edits itself:
- The decided sale gets a verdict marker in the text (e.g. `✓` or `✗` after the address)
- That sale's button row is removed
- Once all sales are decided, all buttons are removed

### 2. Domain Listing Links

**Source:** Domain suggest API (`/v1/properties/_suggest`) already called during enrichment for `year_built`. Also returns `relativeUrl`.

**Storage:** New column `domain_url TEXT` in `sale_classifications` table.

**URL construction:** `https://www.domain.com.au{relativeUrl}`

**Fallback:** If suggest API returns no result, construct a Google search link: `https://www.google.com/search?q={address}+{suburb}+sold`

### 3. Improved Domain Ingest

**Current:** Only uses `/v1/salesResults/{suburb}` — misses some sales.

**Add:** Also query `/v1/listings/residential/_search` with `listingType=Sold` for each segment's suburb/property type. Deduplicate against existing provisional sales by normalised address.

**Frequency:** Consider running Domain ingest daily (lightweight API calls) while keeping VG ingest weekly.

### 4. Enrichment Error Visibility

- When `year_built` lookup fails: display "Year unknown" in digest instead of omitting
- When zoning API returns multiple/ambiguous results: display "Zoning unverified" instead of picking first result
- These labels are visible in the review message so the user knows data quality

### 5. Callback Handling

**Cloudflare Worker (`webhook/worker.js`):**

Updated to handle batched message callbacks:
- Parse callback data `review:{segment}:{sale_id_or_all}:{yes|no}`
- For individual: update DB, edit message to mark that sale's verdict, remove that button row
- For "all": update all sales in that message, remove all buttons, mark all verdicts
- Trigger GitHub Actions `repository_dispatch` to persist to DB

**Remove:**
- 6h poll workflow (`review-poll.yml`) — Worker handles callbacks immediately
- Individual review message sending (`send_review_with_buttons` for single sales)

### 6. What Doesn't Change

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
| `src/tracker/enrich/pipeline.py` | Store `domain_url` from suggest API |
| `src/tracker/enrich/domain.py` | Return `relativeUrl` alongside `year_built` |
| `src/tracker/db.py` | Add `domain_url` column to `sale_classifications` |
| `src/tracker/cli.py` | Update `review-buttons` command to send digests |
| `src/tracker/ingest/domain_sold.py` | Add `/v1/listings/residential/_search` data source |
| `webhook/worker.js` | Handle batched callbacks, edit message with verdicts |
| `config.yml` | No changes |
| `.github/workflows/weekly-report.yml` | Update review step to use digest |
| `.github/workflows/review-poll.yml` | Remove (replaced by Worker) |

## Implementation Order

1. DB schema: add `domain_url` column
2. Enrichment: capture `domain_url` from suggest API, improve error labelling
3. Domain ingest: add second API endpoint
4. Telegram digest: new `send_review_digest()` with batched buttons + links
5. Cloudflare Worker: handle batched callbacks
6. CLI: update `review-buttons` to send digests
7. Cleanup: remove old single-sale review flow, remove poll workflow
