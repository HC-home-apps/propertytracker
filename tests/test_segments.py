# tests/test_segments.py
"""Tests for segment definitions."""

import pytest
from tracker.compute.segments import (
    SEGMENTS,
    Segment,
    get_segment,
    get_segment_for_sale,
    get_proxy_segments,
    get_target_segments,
    is_in_segment,
    get_all_tracked_suburbs,
    get_outpacing_pairs,
)


class TestSegmentDefinitions:
    """Test segment definitions are complete."""

    def test_all_required_segments_exist(self):
        """All required segments are defined."""
        required = [
            'revesby_houses',
            'wollstonecraft_units',
            'wollstonecraft_211',
            'lane_cove_houses',
            'lane_cove_units',
            'chatswood_houses',
            'chatswood_units',
        ]
        for code in required:
            assert code in SEGMENTS, f"Missing segment: {code}"

    def test_revesby_houses_is_proxy(self):
        """Revesby houses is marked as proxy."""
        segment = SEGMENTS['revesby_houses']
        assert segment.is_proxy is True
        assert segment.is_target is False

    def test_lane_cove_houses_is_target(self):
        """Lane Cove houses is marked as target."""
        segment = SEGMENTS['lane_cove_houses']
        assert segment.is_target is True
        assert segment.is_proxy is False

    def test_suburbs_are_lowercase(self):
        """All suburb names are lowercase."""
        for segment in SEGMENTS.values():
            for suburb in segment.suburbs:
                assert suburb == suburb.lower(), f"Suburb not lowercase: {suburb}"


class TestGetSegment:
    """Test get_segment function."""

    def test_returns_segment_for_valid_code(self):
        """Returns segment for valid code."""
        segment = get_segment('revesby_houses')
        assert segment is not None
        assert segment.code == 'revesby_houses'

    def test_returns_none_for_invalid_code(self):
        """Returns None for invalid code."""
        assert get_segment('invalid_segment') is None


class TestGetSegmentForSale:
    """Test automatic segment assignment."""

    def test_revesby_house(self):
        """Revesby house maps to revesby_houses."""
        assert get_segment_for_sale('Revesby', 'house') == 'revesby_houses'

    def test_revesby_heights_house(self):
        """Revesby Heights house maps to revesby_houses."""
        assert get_segment_for_sale('Revesby Heights', 'house') == 'revesby_houses'

    def test_wollstonecraft_unit(self):
        """Wollstonecraft unit maps to wollstonecraft_units."""
        assert get_segment_for_sale('Wollstonecraft', 'unit') == 'wollstonecraft_units'

    def test_lane_cove_house(self):
        """Lane Cove house maps to lane_cove_houses."""
        assert get_segment_for_sale('Lane Cove', 'house') == 'lane_cove_houses'

    def test_lane_cove_north_house(self):
        """Lane Cove North house maps to lane_cove_houses."""
        assert get_segment_for_sale('Lane Cove North', 'house') == 'lane_cove_houses'

    def test_chatswood_unit(self):
        """Chatswood unit maps to chatswood_units."""
        assert get_segment_for_sale('Chatswood', 'unit') == 'chatswood_units'

    def test_unknown_suburb_returns_none(self):
        """Unknown suburb returns None."""
        assert get_segment_for_sale('Sydney', 'house') is None

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        assert get_segment_for_sale('REVESBY', 'house') == 'revesby_houses'

    def test_whitespace_stripped(self):
        """Whitespace is stripped."""
        assert get_segment_for_sale('  Revesby  ', 'house') == 'revesby_houses'


class TestGetProxySegments:
    """Test proxy segment retrieval."""

    def test_returns_proxy_segments(self):
        """Returns segments marked as proxy."""
        proxies = get_proxy_segments()
        codes = {s.code for s in proxies}

        assert 'revesby_houses' in codes
        assert 'wollstonecraft_units' in codes
        assert 'wollstonecraft_211' in codes

    def test_excludes_target_segments(self):
        """Does not return target segments."""
        proxies = get_proxy_segments()
        codes = {s.code for s in proxies}

        assert 'lane_cove_houses' not in codes
        assert 'chatswood_houses' not in codes


class TestGetTargetSegments:
    """Test target segment retrieval."""

    def test_returns_target_segments(self):
        """Returns segments marked as target."""
        targets = get_target_segments()
        codes = {s.code for s in targets}

        assert 'lane_cove_houses' in codes
        assert 'lane_cove_units' in codes
        assert 'chatswood_houses' in codes
        assert 'chatswood_units' in codes

    def test_excludes_proxy_segments(self):
        """Does not return proxy segments."""
        targets = get_target_segments()
        codes = {s.code for s in targets}

        assert 'revesby_houses' not in codes
        assert 'wollstonecraft_units' not in codes


class TestIsInSegment:
    """Test segment membership checking."""

    def test_revesby_in_revesby_houses(self):
        """Revesby house is in revesby_houses."""
        assert is_in_segment('Revesby', 'house', 'revesby_houses') is True

    def test_revesby_unit_not_in_revesby_houses(self):
        """Revesby unit is not in revesby_houses."""
        assert is_in_segment('Revesby', 'unit', 'revesby_houses') is False

    def test_invalid_segment_returns_false(self):
        """Invalid segment code returns False."""
        assert is_in_segment('Revesby', 'house', 'invalid') is False


class TestGetAllTrackedSuburbs:
    """Test suburb collection."""

    def test_includes_all_suburbs(self):
        """Returns all tracked suburbs."""
        suburbs = get_all_tracked_suburbs()

        assert 'revesby' in suburbs
        assert 'wollstonecraft' in suburbs
        assert 'lane cove' in suburbs
        assert 'chatswood' in suburbs
        assert 'lane cove north' in suburbs
        assert 'chatswood west' in suburbs


class TestGetOutpacingPairs:
    """Test outpacing pair generation."""

    def test_returns_house_pairs(self):
        """Returns Revesby vs target houses pairs."""
        pairs = get_outpacing_pairs()

        assert ('revesby_houses', 'lane_cove_houses') in pairs
        assert ('revesby_houses', 'chatswood_houses') in pairs

    def test_returns_unit_pairs(self):
        """Returns Wollstonecraft vs target units pairs."""
        pairs = get_outpacing_pairs()

        assert ('wollstonecraft_units', 'lane_cove_units') in pairs
        assert ('wollstonecraft_units', 'chatswood_units') in pairs

    def test_correct_count(self):
        """Returns expected number of pairs."""
        pairs = get_outpacing_pairs()
        # 2 house pairs + 2 unit pairs = 4
        assert len(pairs) == 4
