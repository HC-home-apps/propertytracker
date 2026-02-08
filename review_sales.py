#!/usr/bin/env python3
"""Quick review script for Revesby comparables."""

import sys
from tracker.db import Database
from tracker.review.telegram import update_review_statuses

# Your property - auto-exclude
YOUR_ADDRESS = "11 Alliance Ave"

db = Database('data/tracker.db')

# Get sales to review (500-600sqm, excluding your property)
rows = db.query('''
    SELECT
        sc.sale_id,
        r.house_number || ' ' || r.street_name as address,
        r.suburb,
        r.purchase_price,
        r.area_sqm,
        r.contract_date
    FROM sale_classifications sc
    JOIN raw_sales r ON sc.sale_id = r.dealing_number
    WHERE sc.review_status = 'pending'
      AND sc.is_auto_excluded = 0
      AND r.area_sqm BETWEEN 500 AND 600
      AND NOT (r.house_number = '11' AND r.street_name = 'Alliance Ave')
    ORDER BY r.contract_date DESC
''')

print(f"\n{'='*60}")
print(f"REVESBY COMPARABLES REVIEW ({len(rows)} sales)")
print(f"{'='*60}")
print("\nFor each sale, enter:")
print("  y = ✅ Comparable (original, R2, good site)")
print("  n = ❌ Not comparable (duplex/modern/bad location)")
print("  s = Skip (review later)")
print("  q = Quit and save progress")
print(f"{'='*60}\n")

approved = []
rejected = []

for i, r in enumerate(rows, 1):
    price = f"${r['purchase_price']:,}"
    area = f"{r['area_sqm']:.0f}sqm"
    date = r['contract_date'][:7]

    addr = r['address'].strip()
    suburb = r['suburb']

    # Domain URL
    search = f"{addr} {suburb}".lower().replace(' ', '-')
    url = f"https://www.domain.com.au/property-profile/{search}-nsw-2212"

    print(f"{i}/{len(rows)}: {addr}, {suburb}")
    print(f"       {price} | {area} | {date}")
    print(f"       {url}")

    while True:
        choice = input("       [y/n/s/q]: ").strip().lower()
        if choice in ['y', 'n', 's', 'q']:
            break
        print("       Invalid. Enter y, n, s, or q")

    if choice == 'q':
        break
    elif choice == 'y':
        approved.append(r['sale_id'])
        print("       → ✅ COMPARABLE\n")
    elif choice == 'n':
        rejected.append(r['sale_id'])
        print("       → ❌ NOT COMPARABLE\n")
    else:
        print("       → Skipped\n")

# Save results
if approved or rejected:
    print(f"\n{'='*60}")
    print("SAVING RESULTS...")

    # Update approved
    if approved:
        for sale_id in approved:
            db.execute('''
                UPDATE sale_classifications
                SET review_status = 'comparable', use_in_median = 1
                WHERE sale_id = ?
            ''', (sale_id,))
        print(f"✅ Marked {len(approved)} as comparable")

    # Update rejected
    if rejected:
        for sale_id in rejected:
            db.execute('''
                UPDATE sale_classifications
                SET review_status = 'not_comparable', use_in_median = 0
                WHERE sale_id = ?
            ''', (sale_id,))
        print(f"❌ Marked {len(rejected)} as not comparable")

    # Show remaining
    remaining = db.query('''
        SELECT COUNT(*) as n FROM sale_classifications
        WHERE review_status = 'pending'
        AND is_auto_excluded = 0
        AND sale_id IN (
            SELECT dealing_number FROM raw_sales
            WHERE area_sqm BETWEEN 500 AND 600
        )
    ''')[0]['n']
    print(f"📋 {remaining} sales still pending review")

db.close()
print("Done!")
