# tests/test_db.py
"""Unit tests for database operations."""

import pytest
from tracker.db import Database


def test_create_tables(temp_db):
    """Database should create all required tables."""
    db = Database(temp_db)
    db.init_schema()
    tables = db.list_tables()

    assert 'raw_sales' in tables
    assert 'property_meta' in tables
    assert 'comp_universe' in tables
    assert 'monthly_metrics' in tables
    assert 'review_queue' in tables
    assert 'run_log' in tables
    assert 'sale_classifications' in tables


def test_raw_sales_insert_and_dedupe(temp_db):
    """Duplicate dealing_number should not create duplicate records."""
    db = Database(temp_db)
    db.init_schema()

    sale = {
        'dealing_number': 'ABC123',
        'property_id': 'LOT1/DP1234',
        'unit_number': None,
        'house_number': '11',
        'street_name': 'ALLIANCE',
        'suburb': 'REVESBY',
        'postcode': '2212',
        'area_sqm': 550.0,
        'zone_code': 'R2',
        'nature_of_property': 'Residence',
        'strata_lot_number': None,
        'contract_date': '2024-01-15',
        'settlement_date': '2024-02-15',
        'purchase_price': 1250000,
        'property_type': 'house',
        'district_code': 108,
        'source_file': 'test.dat',
    }

    # First insert should succeed
    count = db.upsert_raw_sales([sale])
    assert count == 1

    # Second insert of same dealing_number should be ignored (dedupe)
    count = db.upsert_raw_sales([sale])
    assert count == 0

    # Verify only 1 record exists
    rows = db.query("SELECT COUNT(*) as cnt FROM raw_sales")
    assert rows[0]['cnt'] == 1


def test_run_log_insert(temp_db):
    """Run log should track execution status."""
    db = Database(temp_db)
    db.init_schema()

    run_id = db.start_run('ingest', 'manual')
    assert run_id is not None

    # Check it's in running state
    rows = db.query("SELECT status FROM run_log WHERE run_id = ?", (run_id,))
    assert rows[0]['status'] == 'running'

    # Complete the run
    db.complete_run(run_id, 'success', records_processed=100)

    rows = db.query("SELECT status, records_processed FROM run_log WHERE run_id = ?", (run_id,))
    assert rows[0]['status'] == 'success'
    assert rows[0]['records_processed'] == 100


def test_get_last_successful_run(temp_db):
    """Should return the most recent successful run."""
    db = Database(temp_db)
    db.init_schema()

    # No runs yet
    result = db.get_last_successful_run()
    assert result is None

    # Start and complete a run
    run_id = db.start_run('ingest', 'manual')
    db.complete_run(run_id, 'success', records_processed=50)

    result = db.get_last_successful_run()
    assert result is not None
    assert result['run_id'] == run_id
    assert result['status'] == 'success'

    # Filter by run_type
    result = db.get_last_successful_run('ingest')
    assert result is not None

    result = db.get_last_successful_run('compute')
    assert result is None


def test_empty_upsert_raw_sales(temp_db):
    """Empty list should return 0 and not error."""
    db = Database(temp_db)
    db.init_schema()

    count = db.upsert_raw_sales([])
    assert count == 0


def test_multiple_sales_insert(temp_db):
    """Should insert multiple distinct sales correctly."""
    db = Database(temp_db)
    db.init_schema()

    sales = [
        {
            'dealing_number': 'SALE001',
            'property_id': 'LOT1/DP1111',
            'unit_number': None,
            'house_number': '10',
            'street_name': 'MAIN',
            'suburb': 'SYDNEY',
            'postcode': '2000',
            'area_sqm': 400.0,
            'zone_code': 'R2',
            'nature_of_property': 'Residence',
            'strata_lot_number': None,
            'contract_date': '2024-01-01',
            'settlement_date': '2024-02-01',
            'purchase_price': 1000000,
            'property_type': 'house',
            'district_code': 100,
            'source_file': 'test.dat',
        },
        {
            'dealing_number': 'SALE002',
            'property_id': 'LOT2/DP2222',
            'unit_number': '5',
            'house_number': '20',
            'street_name': 'SECOND',
            'suburb': 'SYDNEY',
            'postcode': '2000',
            'area_sqm': 80.0,
            'zone_code': 'R4',
            'nature_of_property': 'Strata Unit',
            'strata_lot_number': 'SP12345',
            'contract_date': '2024-01-15',
            'settlement_date': '2024-02-15',
            'purchase_price': 800000,
            'property_type': 'unit',
            'district_code': 100,
            'source_file': 'test.dat',
        },
    ]

    count = db.upsert_raw_sales(sales)
    assert count == 2

    rows = db.query("SELECT COUNT(*) as cnt FROM raw_sales")
    assert rows[0]['cnt'] == 2


def test_run_log_failed_status(temp_db):
    """Run log should handle failed status with error message."""
    db = Database(temp_db)
    db.init_schema()

    run_id = db.start_run('compute', 'scheduled')
    db.complete_run(run_id, 'failed', error_message='Test error occurred')

    rows = db.query("SELECT status, error_message FROM run_log WHERE run_id = ?", (run_id,))
    assert rows[0]['status'] == 'failed'
    assert rows[0]['error_message'] == 'Test error occurred'


def test_database_close(temp_db):
    """Database connection should close properly."""
    db = Database(temp_db)
    db.init_schema()
    db.close()

    # After closing, a new connection should be created on next operation
    tables = db.list_tables()
    assert len(tables) >= 6
    db.close()


class TestSaleClassificationsTable:
    """Test sale_classifications table exists and works."""

    def test_table_exists(self, temp_db):
        """sale_classifications table is created."""
        db = Database(temp_db)
        db.init_schema()
        tables = db.list_tables()
        assert 'sale_classifications' in tables
        db.close()

    def test_insert_classification(self, temp_db):
        """Can insert and query a classification."""
        db = Database(temp_db)
        db.init_schema()
        db.execute("""
            INSERT INTO sale_classifications (
                sale_id, address, zoning, year_built, has_duplex_keywords,
                is_auto_excluded, auto_exclude_reason, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ('sale_123', '15 Smith St Revesby', 'R2', 1965, False, False, None, 'pending'))

        rows = db.query("SELECT * FROM sale_classifications WHERE sale_id = ?", ('sale_123',))
        assert len(rows) == 1
        assert rows[0]['address'] == '15 Smith St Revesby'
        assert rows[0]['zoning'] == 'R2'
        assert rows[0]['year_built'] == 1965
        assert rows[0]['review_status'] == 'pending'
        assert rows[0]['use_in_median'] == 0  # Default FALSE
        db.close()


class TestProvisionalSalesTable:
    """Test provisional_sales table operations."""

    def test_table_exists(self, db):
        """provisional_sales table should be created by init_schema."""
        tables = db.list_tables()
        assert 'provisional_sales' in tables

    def test_insert_provisional_sale(self, db):
        """Should insert a provisional sale record."""
        inserted = db.upsert_provisional_sales([{
            'id': 'domain-12345',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27-29',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '9|27-29|morton st|wollstonecraft|2065',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{"test": true}',
        }])
        assert inserted == 1

    def test_provisional_dedup_on_id(self, db):
        """Should ignore duplicate provisional sales by id."""
        sale = {
            'id': 'domain-12345',
            'source': 'domain',
            'unit_number': '9',
            'house_number': '27-29',
            'street_name': 'Morton St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 1200000,
            'sold_date': '2026-02-03',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '9|27-29|morton st|wollstonecraft|2065',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{}',
        }
        db.upsert_provisional_sales([sale])
        inserted = db.upsert_provisional_sales([sale])
        assert inserted == 0

    def test_get_unconfirmed_provisional(self, db):
        """Should return only unconfirmed provisional sales."""
        db.upsert_provisional_sales([{
            'id': 'domain-111',
            'source': 'domain',
            'unit_number': None,
            'house_number': '10',
            'street_name': 'Smith St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 900000,
            'sold_date': '2026-01-15',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '|10|smith st|wollstonecraft|2065',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{}',
        }])
        results = db.get_unconfirmed_provisional_sales()
        assert len(results) == 1
        assert results[0]['id'] == 'domain-111'
        assert results[0]['status'] == 'unconfirmed'

    def test_mark_provisional_confirmed(self, db):
        """Should link provisional sale to VG dealing number."""
        db.upsert_provisional_sales([{
            'id': 'domain-222',
            'source': 'domain',
            'unit_number': None,
            'house_number': '10',
            'street_name': 'Smith St',
            'suburb': 'Wollstonecraft',
            'postcode': '2065',
            'property_type': 'unit',
            'sold_price': 900000,
            'sold_date': '2026-01-15',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': '|10|smith st|wollstonecraft|2065',
            'listing_url': None,
            'source_site': None,
            'status': 'unconfirmed',
            'raw_json': '{}',
        }])
        db.mark_provisional_confirmed('domain-222', 'AU123456')
        results = db.get_unconfirmed_provisional_sales()
        assert len(results) == 0

    def test_get_provisional_for_report(self, db):
        """Should return unconfirmed sales filtered by suburb."""
        db.upsert_provisional_sales([
            {
                'id': 'domain-aaa',
                'source': 'domain',
                'unit_number': '9',
                'house_number': '27',
                'street_name': 'Morton St',
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
            },
            {
                'id': 'domain-bbb',
                'source': 'domain',
                'unit_number': None,
                'house_number': '5',
                'street_name': 'Smith St',
                'suburb': 'Revesby',
                'postcode': '2212',
                'property_type': 'house',
                'sold_price': 1500000,
                'sold_date': '2026-02-01',
                'bedrooms': None,
                'bathrooms': None,
                'car_spaces': None,
                'address_normalised': '|5|smith st|revesby|2212',
                'listing_url': None,
                'source_site': None,
                'status': 'unconfirmed',
                'raw_json': '{}',
            },
        ])
        results = db.get_unconfirmed_provisional_sales(suburb='Wollstonecraft')
        assert len(results) == 1
        assert results[0]['suburb'] == 'Wollstonecraft'


class TestCleanupProvisionalSales:
    """Test cleanup_provisional_sales removes bad records."""

    def _make_sale(self, id, house_number='5', street_name='Smith St',
                   unit_number=None, suburb='Revesby', addr_norm=None):
        return {
            'id': id,
            'source': 'google',
            'unit_number': unit_number,
            'house_number': house_number,
            'street_name': street_name,
            'suburb': suburb,
            'postcode': '2212',
            'property_type': 'house',
            'sold_price': 1000000,
            'sold_date': '2026-01-15',
            'bedrooms': None,
            'bathrooms': None,
            'car_spaces': None,
            'address_normalised': addr_norm or f'|{house_number}|{street_name.lower()}|{suburb.lower()}|2212',
            'listing_url': '',
            'source_site': '',
            'status': 'unconfirmed',
            'raw_json': '{}',
        }

    def test_removes_no_address_records(self, db):
        """Should remove records with no house_number and no unit_number."""
        db.upsert_provisional_sales([
            self._make_sale('google-good', house_number='10'),
            self._make_sale('google-bad', house_number=None, addr_norm='||smith st|revesby|2212'),
            self._make_sale('google-bad2', house_number='', addr_norm='||jones st|revesby|2212'),
        ])
        deleted = db.cleanup_provisional_sales()
        assert deleted >= 2
        remaining = db.query("SELECT id FROM provisional_sales")
        ids = [r['id'] for r in remaining]
        assert 'google-good' in ids
        assert 'google-bad' not in ids
        assert 'google-bad2' not in ids

    def test_removes_aggregate_titles(self, db):
        """Should remove records with aggregate page titles as street names."""
        db.upsert_provisional_sales([
            self._make_sale('google-good', street_name='Smith St'),
            self._make_sale('google-agg1', street_name='19824 Properties sold in Revesby'),
            self._make_sale('google-agg2', street_name='12063 Houses sold in Revesby'),
        ])
        deleted = db.cleanup_provisional_sales()
        assert deleted >= 2
        remaining = db.query("SELECT id FROM provisional_sales")
        ids = [r['id'] for r in remaining]
        assert 'google-good' in ids
        assert 'google-agg1' not in ids

    def test_removes_unparsed_title_text(self, db):
        """Should remove records with NSW or long text in street_name."""
        db.upsert_provisional_sales([
            self._make_sale('google-good', street_name='Smith St'),
            self._make_sale('google-nsw', street_name='32 Beaconsfield Street, Revesby NSW 2212 on 30 Jan 2026'),
        ])
        deleted = db.cleanup_provisional_sales()
        assert deleted >= 1
        remaining = db.query("SELECT id FROM provisional_sales")
        ids = [r['id'] for r in remaining]
        assert 'google-good' in ids
        assert 'google-nsw' not in ids

    def test_removes_duplicates_keeps_one(self, db):
        """Should deduplicate by address_normalised, keeping one."""
        db.upsert_provisional_sales([
            self._make_sale('google-dup1', addr_norm='|5|smith st|revesby|2212'),
            self._make_sale('google-dup2', addr_norm='|5|smith st|revesby|2212'),
        ])
        deleted = db.cleanup_provisional_sales()
        assert deleted >= 1
        remaining = db.query("SELECT id FROM provisional_sales")
        assert len(remaining) == 1

    def test_keeps_valid_records(self, db):
        """Should not delete valid records."""
        db.upsert_provisional_sales([
            self._make_sale('google-valid1', house_number='10', street_name='Smith St',
                           addr_norm='|10|smith st|revesby|2212'),
            self._make_sale('google-valid2', house_number='15', street_name='Jones Ave',
                           addr_norm='|15|jones ave|revesby|2212'),
        ])
        deleted = db.cleanup_provisional_sales()
        assert deleted == 0
        remaining = db.query("SELECT id FROM provisional_sales")
        assert len(remaining) == 2
