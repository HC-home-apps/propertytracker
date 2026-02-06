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
