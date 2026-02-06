# tests/test_matcher.py
import pytest
from tracker.db import Database
from tracker.ingest.matcher import match_provisional_to_vg


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / 'test.db'))
    db.init_schema()
    return db


class TestMatchProvisionalToVG:
    """Test address-based matching of Domain sales to VG records."""

    def test_matches_exact_address(self, db):
        """Should match when normalised addresses are identical."""
        db.upsert_raw_sales([{
            'dealing_number': 'AU999999',
            'property_id': '791136.0',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'area_sqm': None,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': '9.0',
            'contract_date': '2026-02-03',
            'settlement_date': '2026-03-15',
            'purchase_price': 1200000,
            'property_type': 'unit',
            'district_code': 118,
            'source_file': 'test.csv',
        }])

        db.upsert_provisional_sales([{
            'id': 'domain-12345',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '9|27|morton st|wollstonecraft|2065',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{}',
        }])

        matched = match_provisional_to_vg(db)
        assert matched == 1

        unconfirmed = db.get_unconfirmed_provisional_sales()
        assert len(unconfirmed) == 0

    def test_no_match_different_address(self, db):
        """Should not match when addresses differ."""
        db.upsert_raw_sales([{
            'dealing_number': 'AU888888',
            'property_id': '',
            'unit_number': '5',
            'house_number': '10',
            'street_name': 'Smith St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'area_sqm': None,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': '5.0',
            'contract_date': '2026-02-03',
            'settlement_date': '2026-03-15',
            'purchase_price': 900000,
            'property_type': 'unit',
            'district_code': 118,
            'source_file': 'test.csv',
        }])

        db.upsert_provisional_sales([{
            'id': 'domain-99999',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '9|27|morton st|wollstonecraft|2065',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{}',
        }])

        matched = match_provisional_to_vg(db)
        assert matched == 0

    def test_no_match_outside_date_window(self, db):
        """Should not match when dates are more than 14 days apart."""
        db.upsert_raw_sales([{
            'dealing_number': 'AU777777',
            'property_id': '',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'area_sqm': None,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': '9.0',
            'contract_date': '2026-03-01',
            'settlement_date': '2026-04-15',
            'purchase_price': 1200000,
            'property_type': 'unit',
            'district_code': 118,
            'source_file': 'test.csv',
        }])

        db.upsert_provisional_sales([{
            'id': 'domain-77777',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27',
            'street_name': 'Morton Street',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-01',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '9|27|morton st|wollstonecraft|2065',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{}',
        }])

        matched = match_provisional_to_vg(db)
        assert matched == 0

    def test_empty_provisional_returns_zero(self, db):
        """Should return 0 when no provisional sales exist."""
        matched = match_provisional_to_vg(db)
        assert matched == 0

    def test_carries_over_review_decision_on_match(self, db):
        """When a reviewed provisional sale matches VG, carry over review to sale_classifications."""
        db.upsert_raw_sales([{
            'dealing_number': 'AU555555',
            'property_id': '123456.0',
            'unit_number': '',
            'house_number': '15',
            'street_name': 'Smith St',
            'suburb': 'Revesby',
            'postcode': '2212',
            'area_sqm': 550,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': None,
            'contract_date': '2026-01-15',
            'settlement_date': '2026-02-15',
            'purchase_price': 920000,
            'property_type': 'house',
            'district_code': 108,
            'source_file': 'test.csv',
        }])

        db.upsert_provisional_sales([{
            'id': 'google-12345',
            'source': 'google',
            'unit_number': '',
            'house_number': '15',
            'street_name': 'Smith Street',
            'suburb': 'Revesby',
            'postcode': '2212',
            'property_type': 'house',
            'sold_price': 920000,
            'sold_date': '2026-01-15',
            'bedrooms': 3,
            'bathrooms': 2,
            'car_spaces': 1,
            'address_normalised': '|15|smith st|revesby|2212',
            'listing_url': 'https://domain.com.au/15-smith-st',
            'source_site': 'domain.com.au',
            'status': 'unconfirmed',
            'raw_json': '{}',
        }])

        # Mark the provisional sale as reviewed (comparable)
        db.execute(
            "UPDATE provisional_sales SET review_status = ?, use_in_median = ?, reviewed_at = ? WHERE id = ?",
            ('comparable', 1, '2026-01-20T00:00:00+00:00', 'google-12345')
        )

        matched = match_provisional_to_vg(db)
        assert matched == 1

        # Check that sale_classifications entry was created with carried-over review
        rows = db.query(
            "SELECT review_status, use_in_median, listing_url FROM sale_classifications WHERE sale_id = ?",
            ('AU555555',)
        )
        assert len(rows) == 1
        assert rows[0]['review_status'] == 'comparable'
        assert rows[0]['use_in_median'] == 1
        assert rows[0]['listing_url'] == 'https://domain.com.au/15-smith-st'

    def test_no_carryover_for_unreviewed_provisional(self, db):
        """When an unreviewed provisional matches VG, should NOT create sale_classifications."""
        db.upsert_raw_sales([{
            'dealing_number': 'AU444444',
            'property_id': '789.0',
            'unit_number': '',
            'house_number': '20',
            'street_name': 'Jones Ave',
            'suburb': 'Revesby',
            'postcode': '2212',
            'area_sqm': 500,
            'zone_code': None,
            'nature_of_property': 'Residence',
            'strata_lot_number': None,
            'contract_date': '2026-01-20',
            'settlement_date': '2026-02-20',
            'purchase_price': 880000,
            'property_type': 'house',
            'district_code': 108,
            'source_file': 'test.csv',
        }])

        db.upsert_provisional_sales([{
            'id': 'google-67890',
            'source': 'google',
            'unit_number': '',
            'house_number': '20',
            'street_name': 'Jones Avenue',
            'suburb': 'Revesby',
            'postcode': '2212',
            'property_type': 'house',
            'sold_price': 880000,
            'sold_date': '2026-01-20',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '|20|jones ave|revesby|2212',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{}',
        }])

        # Provisional is still 'pending' (default), so no carryover should happen
        matched = match_provisional_to_vg(db)
        assert matched == 1

        # sale_classifications should NOT have been created
        rows = db.query(
            "SELECT * FROM sale_classifications WHERE sale_id = ?",
            ('AU444444',)
        )
        assert len(rows) == 0
