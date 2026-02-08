#!/usr/bin/env python3
"""Apply review decisions from user response."""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

from tracker.db import Database

db = Database('data/tracker.db')

# Get sales in same order as sent
rows = db.query('''
    SELECT
        sc.sale_id,
        r.house_number || ' ' || r.street_name as address,
        r.suburb,
        r.purchase_price
    FROM sale_classifications sc
    JOIN raw_sales r ON sc.sale_id = r.dealing_number
    WHERE sc.review_status = 'pending'
      AND sc.is_auto_excluded = 0
      AND r.area_sqm BETWEEN 500 AND 600
      AND NOT (r.house_number = '11' AND r.street_name = 'Alliance Ave')
    ORDER BY r.contract_date DESC
''')

print(f'Sales to review: {len(rows)}')

# User's response
response = 'nyyynyyyynyyyynynnnynynynnynnnnynynyyyy'
print(f'Response length: {len(response)}')

if len(response) != len(rows):
    print(f'Mismatch! Expected {len(rows)}, got {len(response)}')
    count = min(len(response), len(rows))
else:
    count = len(rows)

# Apply reviews
approved = 0
rejected = 0

for i in range(count):
    sale_id = rows[i]['sale_id']
    choice = response[i]

    if choice == 'y':
        db.execute('''
            UPDATE sale_classifications
            SET review_status = 'comparable', use_in_median = 1
            WHERE sale_id = ?
        ''', (sale_id,))
        approved += 1
    else:
        db.execute('''
            UPDATE sale_classifications
            SET review_status = 'not_comparable', use_in_median = 0
            WHERE sale_id = ?
        ''', (sale_id,))
        rejected += 1

print()
print(f'✅ Approved as comparable: {approved}')
print(f'❌ Rejected: {rejected}')

# Show summary of approved sales
print()
print('Approved comparables (by price):')
approved_rows = db.query('''
    SELECT sc.address, r.purchase_price, r.area_sqm
    FROM sale_classifications sc
    JOIN raw_sales r ON sc.sale_id = r.dealing_number
    WHERE sc.review_status = 'comparable'
      AND sc.use_in_median = 1
      AND r.area_sqm BETWEEN 500 AND 600
    ORDER BY r.purchase_price DESC
''')
for r in approved_rows:
    print(f'  ${r["purchase_price"]:,} - {r["address"]}')

# Calculate median
prices = [r['purchase_price'] for r in approved_rows]
if prices:
    prices.sort()
    mid = len(prices) // 2
    if len(prices) % 2 == 0:
        median = (prices[mid-1] + prices[mid]) // 2
    else:
        median = prices[mid]
    print()
    print(f'📊 VERIFIED MEDIAN: ${median:,} (n={len(prices)} comparables)')

db.close()
