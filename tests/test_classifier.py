# tests/test_classifier.py
"""Tests for sale classification logic."""

import pytest
from tracker.enrich.classifier import (
    has_exclude_keywords,
    should_auto_exclude,
    EXCLUDE_KEYWORDS,
)


class TestKeywordScanner:
    """Test keyword detection for auto-exclusion."""

    def test_detects_duplex(self):
        """Detects 'duplex' keyword."""
        assert has_exclude_keywords("Brand new duplex on corner block") is True

    def test_detects_dual_occ(self):
        """Detects 'dual occ' keyword."""
        assert has_exclude_keywords("Dual occ approved site") is True

    def test_detects_torrens(self):
        """Detects 'torrens' keyword (title type for duplexes)."""
        assert has_exclude_keywords("Torrens title half") is True

    def test_detects_brand_new(self):
        """Detects 'brand new' keyword."""
        assert has_exclude_keywords("Brand new 4 bedroom home") is True

    def test_detects_just_completed(self):
        """Detects 'just completed' keyword."""
        assert has_exclude_keywords("Just completed modern residence") is True

    def test_case_insensitive(self):
        """Keywords detected regardless of case."""
        assert has_exclude_keywords("DUPLEX opportunity") is True
        assert has_exclude_keywords("Dual OCC potential") is True

    def test_no_keywords_returns_false(self):
        """Returns False when no keywords present."""
        assert has_exclude_keywords("Original fibro home on 556sqm") is False
        assert has_exclude_keywords("3 bedroom house in quiet street") is False

    def test_empty_string(self):
        """Handles empty string."""
        assert has_exclude_keywords("") is False

    def test_none_description(self):
        """Handles None description."""
        assert has_exclude_keywords(None) is False


class TestAutoExcludeDecision:
    """Test combined auto-exclude logic."""

    def test_excludes_non_r2_zoning(self):
        """Excludes properties not zoned R2 or R3."""
        excluded, reason = should_auto_exclude(zoning='B1', year_built=1970, has_keywords=False)
        assert excluded is True
        assert 'non-R2' in reason

    def test_allows_r2_zoning(self):
        """Allows R2 zoning."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=1970, has_keywords=False)
        assert excluded is False

    def test_allows_r3_zoning(self):
        """Allows R3 zoning (medium density)."""
        excluded, reason = should_auto_exclude(zoning='R3', year_built=1970, has_keywords=False)
        assert excluded is False

    def test_excludes_modern_build(self):
        """Excludes properties built after 2010."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=2018, has_keywords=False)
        assert excluded is True
        assert '2018' in reason

    def test_allows_old_build(self):
        """Allows properties built before 2010."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=1965, has_keywords=False)
        assert excluded is False

    def test_excludes_duplex_keywords(self):
        """Excludes when duplex keywords present."""
        excluded, reason = should_auto_exclude(zoning='R2', year_built=1970, has_keywords=True)
        assert excluded is True
        assert 'duplex' in reason.lower()

    def test_handles_none_values(self):
        """Handles None for zoning and year_built."""
        excluded, reason = should_auto_exclude(zoning=None, year_built=None, has_keywords=False)
        assert excluded is False  # Can't exclude without data
