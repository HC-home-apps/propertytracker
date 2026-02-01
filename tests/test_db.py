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
