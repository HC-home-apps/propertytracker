# tests/test_enrich_pipeline.py
"""Tests for enrichment pipeline orchestrator."""

import pytest
from unittest.mock import patch, Mock, MagicMock
import tempfile
import os

from tracker.db import Database
from tracker.enrich.pipeline import (
    enrich_sale,
    classify_sale,
    process_pending_sales,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(db_path=path)
    db.init_schema()
    # Insert a test sale
    db.execute("""
        INSERT INTO raw_sales (
            dealing_number, property_id, street_name, suburb, postcode,
            area_sqm, contract_date, purchase_price, property_type, district_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ('DN123', '', 'Smith St', 'Revesby', '2212', 556, '2025-01-15', 1420000, 'house', 108))
    yield db
    db.close()
    os.unlink(path)


class TestEnrichSale:
    """Test individual sale enrichment."""

    @patch('tracker.enrich.pipeline.get_zoning')
    @patch('tracker.enrich.pipeline.get_year_built')
    def test_enriches_with_zoning_and_year(self, mock_year, mock_zoning):
        """Enriches sale with zoning and year built."""
        mock_zoning.return_value = 'R2'
        mock_year.return_value = 1965

        result = enrich_sale(
            address="15 Smith St, Revesby NSW 2212",
            suburb="Revesby",
            postcode="2212",
            description=None,
            api_key="test_key",
        )

        assert result['zoning'] == 'R2'
        assert result['year_built'] == 1965
        assert result['has_duplex_keywords'] is False

    def test_detects_keywords_in_description(self):
        """Detects duplex keywords in description."""
        result = enrich_sale(
            address="15 Smith St, Revesby NSW 2212",
            suburb="Revesby",
            postcode="2212",
            description="Brand new duplex",
            api_key=None,
        )

        assert result['has_duplex_keywords'] is True


class TestClassifySale:
    """Test sale classification logic."""

    def test_auto_excludes_non_r2(self):
        """Auto-excludes non-R2 zoning."""
        enrichment = {
            'zoning': 'B1',
            'year_built': 1970,
            'has_duplex_keywords': False,
        }

        result = classify_sale(enrichment)

        assert result['is_auto_excluded'] is True
        assert 'non-R2' in result['auto_exclude_reason']
        assert result['review_status'] == 'not_comparable'
        assert result['use_in_median'] is False

    def test_marks_pending_for_valid_sale(self):
        """Marks valid sale as pending review."""
        enrichment = {
            'zoning': 'R2',
            'year_built': 1965,
            'has_duplex_keywords': False,
        }

        result = classify_sale(enrichment)

        assert result['is_auto_excluded'] is False
        assert result['review_status'] == 'pending'
        assert result['use_in_median'] is False  # Until manually approved


class TestEnrichmentErrorLabels:
    """Test enrichment error labeling."""

    @patch('tracker.enrich.pipeline.get_zoning')
    @patch('tracker.enrich.pipeline.get_year_built')
    def test_labels_unknown_year(self, mock_year, mock_zoning):
        """Should label unknown year."""
        mock_zoning.return_value = 'R2'
        mock_year.return_value = None

        enrichment = enrich_sale('15 Smith St', 'Revesby', '2212')
        assert enrichment['year_built'] is None
        assert enrichment['year_built_label'] == 'Year unknown'

    @patch('tracker.enrich.pipeline.get_zoning')
    def test_labels_unverified_zoning(self, mock_zoning):
        """Should label unverified zoning."""
        mock_zoning.return_value = None

        enrichment = enrich_sale('15 Smith St', 'Revesby', '2212')
        assert enrichment['zoning_label'] == 'Zoning unverified'

    @patch('tracker.enrich.pipeline.get_zoning')
    @patch('tracker.enrich.pipeline.get_year_built')
    def test_labels_known_values(self, mock_year, mock_zoning):
        """Should label known year and zoning."""
        mock_zoning.return_value = 'R2'
        mock_year.return_value = 1965

        enrichment = enrich_sale('15 Smith St', 'Revesby', '2212', api_key='test-key')
        assert enrichment['year_built'] == 1965
        assert enrichment['year_built_label'] == 'Built 1965'
        assert enrichment['zoning'] == 'R2'
        assert enrichment['zoning_label'] == 'R2'
