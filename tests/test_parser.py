# tests/test_parser.py
"""Tests for CSV parser module."""

import pytest
from pathlib import Path
from tracker.ingest.parser import (
    parse_csv_file,
    classify_property_type,
    _parse_date,
    _safe_int,
    _safe_float,
    _parse_row,
)


class TestClassifyPropertyType:
    """Test property type classification."""

    def test_strata_lot_is_unit(self):
        """Strata lot number indicates unit."""
        assert classify_property_type('SP12345', 'Residence') == 'unit'

    def test_no_strata_residence_is_house(self):
        """Residence without strata is house."""
        assert classify_property_type('', 'Residence') == 'house'

    def test_vacant_land(self):
        """Vacant land classification."""
        assert classify_property_type('', 'Vacant Land') == 'land'

    def test_unit_in_nature(self):
        """Unit in nature field."""
        assert classify_property_type('', 'Unit') == 'unit'

    def test_flat_in_nature(self):
        """Flat in nature field."""
        assert classify_property_type('', 'Flat') == 'unit'

    def test_house_in_nature(self):
        """House in nature field."""
        assert classify_property_type('', 'House') == 'house'

    def test_commercial_is_other(self):
        """Commercial property is other."""
        assert classify_property_type('', 'Commercial') == 'other'

    def test_empty_defaults_to_house(self):
        """Empty fields default to house."""
        assert classify_property_type('', '') == 'house'


class TestParseDate:
    """Test date parsing."""

    def test_iso_format(self):
        """Parse ISO date format."""
        assert _parse_date('2024-01-15') == '2024-01-15'

    def test_au_format(self):
        """Parse Australian date format."""
        assert _parse_date('15/01/2024') == '2024-01-15'

    def test_au_format_with_dashes(self):
        """Parse AU format with dashes."""
        assert _parse_date('15-01-2024') == '2024-01-15'

    def test_empty_returns_none(self):
        """Empty string returns None."""
        assert _parse_date('') is None

    def test_invalid_returns_none(self):
        """Invalid date returns None."""
        assert _parse_date('not-a-date') is None

    def test_whitespace_trimmed(self):
        """Whitespace is trimmed."""
        assert _parse_date('  2024-01-15  ') == '2024-01-15'


class TestSafeInt:
    """Test safe integer parsing."""

    def test_simple_number(self):
        """Parse simple integer."""
        assert _safe_int('123') == 123

    def test_comma_formatted(self):
        """Parse comma-formatted number."""
        assert _safe_int('1,234,567') == 1234567

    def test_dollar_sign(self):
        """Handle dollar sign."""
        assert _safe_int('$1,500,000') == 1500000

    def test_float_truncates(self):
        """Float values are truncated."""
        assert _safe_int('123.45') == 123

    def test_empty_returns_zero(self):
        """Empty string returns 0."""
        assert _safe_int('') == 0

    def test_invalid_returns_zero(self):
        """Invalid string returns 0."""
        assert _safe_int('abc') == 0


class TestSafeFloat:
    """Test safe float parsing."""

    def test_simple_float(self):
        """Parse simple float."""
        assert _safe_float('123.45') == 123.45

    def test_integer_as_float(self):
        """Parse integer as float."""
        assert _safe_float('100') == 100.0

    def test_comma_formatted(self):
        """Parse comma-formatted number."""
        assert _safe_float('1,234.56') == 1234.56

    def test_empty_returns_none(self):
        """Empty string returns None."""
        assert _safe_float('') is None

    def test_invalid_returns_none(self):
        """Invalid string returns None."""
        assert _safe_float('abc') is None


class TestParseCsvFile:
    """Test CSV file parsing."""

    def test_parses_valid_csv(self, tmp_path):
        """Parse a valid CSV file."""
        csv_content = """dealing_number,property_id,unit_number,house_number,street_name,suburb,postcode,area,zone_code,nature_of_property,strata_lot_number,contract_date,settlement_date,purchase_price,district_code
DN123456,P001,,11,Alliance Ave,Revesby,2212,500,R2,Residence,,2024-01-15,2024-02-15,1500000,108
DN123457,P002,2,10,Smith St,Wollstonecraft,2065,80,R3,Residence,SP12345,2024-01-20,2024-02-20,850000,118
"""
        csv_file = tmp_path / 'test.csv'
        csv_file.write_text(csv_content)

        records = list(parse_csv_file(csv_file, districts={108, 118}, suburbs={'revesby', 'wollstonecraft'}))

        assert len(records) == 2

        # Check first record (house)
        assert records[0]['dealing_number'] == 'DN123456'
        assert records[0]['suburb'] == 'Revesby'
        assert records[0]['purchase_price'] == 1500000
        assert records[0]['property_type'] == 'house'

        # Check second record (unit)
        assert records[1]['dealing_number'] == 'DN123457'
        assert records[1]['unit_number'] == '2'
        assert records[1]['property_type'] == 'unit'

    def test_filters_by_district(self, tmp_path):
        """Filter by district code."""
        csv_content = """dealing_number,house_number,street_name,suburb,postcode,contract_date,purchase_price,district_code,nature_of_property,strata_lot_number
DN001,1,Test St,Revesby,2212,2024-01-15,1000000,108,Residence,
DN002,2,Other St,Other,2000,2024-01-15,2000000,999,Residence,
"""
        csv_file = tmp_path / 'test.csv'
        csv_file.write_text(csv_content)

        records = list(parse_csv_file(csv_file, districts={108}, suburbs=None))

        assert len(records) == 1
        assert records[0]['dealing_number'] == 'DN001'

    def test_filters_by_suburb(self, tmp_path):
        """Filter by suburb name."""
        csv_content = """dealing_number,house_number,street_name,suburb,postcode,contract_date,purchase_price,district_code,nature_of_property,strata_lot_number
DN001,1,Test St,Revesby,2212,2024-01-15,1000000,108,Residence,
DN002,2,Other St,Sydney,2000,2024-01-15,2000000,108,Residence,
"""
        csv_file = tmp_path / 'test.csv'
        csv_file.write_text(csv_content)

        records = list(parse_csv_file(csv_file, districts=None, suburbs={'revesby'}))

        assert len(records) == 1
        assert records[0]['suburb'] == 'Revesby'

    def test_skips_invalid_price(self, tmp_path):
        """Skip records with invalid price."""
        csv_content = """dealing_number,house_number,street_name,suburb,postcode,contract_date,purchase_price,district_code,nature_of_property,strata_lot_number
DN001,1,Test St,Revesby,2212,2024-01-15,0,108,Residence,
DN002,2,Test St,Revesby,2212,2024-01-15,1000000,108,Residence,
"""
        csv_file = tmp_path / 'test.csv'
        csv_file.write_text(csv_content)

        records = list(parse_csv_file(csv_file, districts={108}, suburbs={'revesby'}))

        assert len(records) == 1
        assert records[0]['dealing_number'] == 'DN002'

    def test_skips_missing_dealing_number(self, tmp_path):
        """Skip records without dealing number."""
        csv_content = """dealing_number,house_number,street_name,suburb,postcode,contract_date,purchase_price,district_code,nature_of_property,strata_lot_number
,1,Test St,Revesby,2212,2024-01-15,1000000,108,Residence,
DN002,2,Test St,Revesby,2212,2024-01-15,1000000,108,Residence,
"""
        csv_file = tmp_path / 'test.csv'
        csv_file.write_text(csv_content)

        records = list(parse_csv_file(csv_file, districts={108}, suburbs={'revesby'}))

        assert len(records) == 1


class TestParseRow:
    """Test individual row parsing."""

    def test_parses_complete_row(self):
        """Parse a complete row."""
        row = {
            'dealing_number': 'DN123',
            'property_id': 'P001',
            'unit_number': '',
            'house_number': '11',
            'street_name': 'Alliance Ave',
            'suburb': 'Revesby',
            'postcode': '2212',
            'area': '500',
            'zone_code': 'R2',
            'nature_of_property': 'Residence',
            'strata_lot_number': '',
            'contract_date': '2024-01-15',
            'settlement_date': '2024-02-15',
            'purchase_price': '1500000',
            'district_code': '108',
        }

        result = _parse_row(row, 'test.csv')

        assert result is not None
        assert result['dealing_number'] == 'DN123'
        assert result['purchase_price'] == 1500000
        assert result['property_type'] == 'house'
        assert result['source_file'] == 'test.csv'

    def test_returns_none_for_missing_dealing(self):
        """Return None if dealing number missing."""
        row = {
            'dealing_number': '',
            'purchase_price': '1000000',
            'contract_date': '2024-01-15',
        }

        result = _parse_row(row, 'test.csv')
        assert result is None

    def test_returns_none_for_invalid_price(self):
        """Return None if price is invalid."""
        row = {
            'dealing_number': 'DN123',
            'purchase_price': '-100',
            'contract_date': '2024-01-15',
        }

        result = _parse_row(row, 'test.csv')
        assert result is None
