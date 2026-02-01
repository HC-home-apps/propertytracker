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
    load_segments_from_config,
    get_default_segments,
    init_segments,
)


class TestSegmentDefinitions:
    """Test segment definitions are complete."""

    def test_default_segments_exist(self):
        """Default segments are defined."""
        default = get_default_segments()
        required = [
            'revesby_houses',
            'wollstonecraft_units',
            'lane_cove_houses',
            'chatswood_houses',
        ]
        for code in required:
            assert code in default, f"Missing default segment: {code}"

    def test_revesby_houses_is_proxy(self):
        """Revesby houses is marked as proxy."""
        segment = get_default_segments()['revesby_houses']
        assert segment.is_proxy is True
        assert segment.is_target is False

    def test_lane_cove_houses_is_target(self):
        """Lane Cove houses is marked as target."""
        segment = get_default_segments()['lane_cove_houses']
        assert segment.is_target is True
        assert segment.is_proxy is False

    def test_suburbs_are_lowercase(self):
        """All suburb names are lowercase."""
        for segment in get_default_segments().values():
            for suburb in segment.suburbs:
                assert suburb == suburb.lower(), f"Suburb not lowercase: {suburb}"


class TestLoadSegmentsFromConfig:
    """Test config-driven segment loading."""

    def test_loads_from_config(self):
        """Loads segments from config dict."""
        config = {
            'segments': {
                'test_houses': {
                    'display_name': 'Test Houses',
                    'suburbs': ['test', 'test north'],
                    'property_type': 'house',
                    'role': 'proxy',
                    'description': 'Test segment',
                }
            }
        }
        segments = load_segments_from_config(config)
        assert 'test_houses' in segments
        assert segments['test_houses'].display_name == 'Test Houses'
        assert 'test' in segments['test_houses'].suburbs

    def test_loads_area_filters(self):
        """Loads area filters from config."""
        config = {
            'segments': {
                'filtered_houses': {
                    'display_name': 'Filtered Houses',
                    'suburbs': ['test'],
                    'property_type': 'house',
                    'role': 'proxy',
                    'filters': {
                        'area_min': 500,
                        'area_max': 600,
                    }
                }
            }
        }
        segments = load_segments_from_config(config)
        assert segments['filtered_houses'].area_min == 500
        assert segments['filtered_houses'].area_max == 600

    def test_loads_street_filters(self):
        """Loads street filters from config."""
        config = {
            'segments': {
                'filtered_units': {
                    'display_name': 'Filtered Units',
                    'suburbs': ['test'],
                    'property_type': 'unit',
                    'role': 'proxy',
                    'filters': {
                        'streets': ['main st', 'second ave'],
                    }
                }
            }
        }
        segments = load_segments_from_config(config)
        assert 'main st' in segments['filtered_units'].streets
        assert 'second ave' in segments['filtered_units'].streets

    def test_falls_back_to_defaults(self):
        """Returns defaults when no segments in config."""
        config = {}
        segments = load_segments_from_config(config)
        assert 'revesby_houses' in segments


class TestSegmentFilterDescription:
    """Test filter description generation."""

    def test_area_filter_description(self):
        """Generates area filter description."""
        segment = Segment(
            code='test',
            display_name='Test',
            suburbs=frozenset(['test']),
            property_type='house',
            role='proxy',
            area_min=500,
            area_max=600,
        )
        desc = segment.get_filter_description()
        assert '500-600sqm land' in desc

    def test_street_filter_description(self):
        """Generates street filter description."""
        segment = Segment(
            code='test',
            display_name='Test',
            suburbs=frozenset(['test']),
            property_type='unit',
            role='proxy',
            streets=frozenset(['main st', 'second ave']),
        )
        desc = segment.get_filter_description()
        assert 'Main St' in desc or 'Second Ave' in desc

    def test_no_filter_returns_none(self):
        """Returns None when no filters."""
        segment = Segment(
            code='test',
            display_name='Test',
            suburbs=frozenset(['test']),
            property_type='house',
            role='proxy',
        )
        assert segment.get_filter_description() is None


class TestGetSegment:
    """Test get_segment function."""

    def test_returns_segment_for_valid_code(self):
        """Returns segment for valid code."""
        init_segments({})  # Use defaults
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
        init_segments({})  # Use defaults
        assert get_segment_for_sale('Revesby', 'house') == 'revesby_houses'

    def test_revesby_heights_house(self):
        """Revesby Heights house maps to revesby_houses."""
        init_segments({})
        assert get_segment_for_sale('Revesby Heights', 'house') == 'revesby_houses'

    def test_wollstonecraft_unit(self):
        """Wollstonecraft unit maps to wollstonecraft_units."""
        init_segments({})
        assert get_segment_for_sale('Wollstonecraft', 'unit') == 'wollstonecraft_units'

    def test_lane_cove_house(self):
        """Lane Cove house maps to lane_cove_houses."""
        init_segments({})
        assert get_segment_for_sale('Lane Cove', 'house') == 'lane_cove_houses'

    def test_lane_cove_north_house(self):
        """Lane Cove North house maps to lane_cove_houses."""
        init_segments({})
        assert get_segment_for_sale('Lane Cove North', 'house') == 'lane_cove_houses'

    def test_unknown_suburb_returns_none(self):
        """Unknown suburb returns None."""
        assert get_segment_for_sale('Sydney', 'house') is None

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        init_segments({})
        assert get_segment_for_sale('REVESBY', 'house') == 'revesby_houses'

    def test_whitespace_stripped(self):
        """Whitespace is stripped."""
        init_segments({})
        assert get_segment_for_sale('  Revesby  ', 'house') == 'revesby_houses'


class TestGetProxySegments:
    """Test proxy segment retrieval."""

    def test_returns_proxy_segments(self):
        """Returns segments marked as proxy."""
        init_segments({})  # Use defaults
        proxies = get_proxy_segments()
        codes = {s.code for s in proxies}

        assert 'revesby_houses' in codes
        assert 'wollstonecraft_units' in codes

    def test_excludes_target_segments(self):
        """Does not return target segments."""
        init_segments({})
        proxies = get_proxy_segments()
        codes = {s.code for s in proxies}

        assert 'lane_cove_houses' not in codes
        assert 'chatswood_houses' not in codes


class TestGetTargetSegments:
    """Test target segment retrieval."""

    def test_returns_target_segments(self):
        """Returns segments marked as target."""
        init_segments({})  # Use defaults
        targets = get_target_segments()
        codes = {s.code for s in targets}

        assert 'lane_cove_houses' in codes
        assert 'chatswood_houses' in codes

    def test_excludes_proxy_segments(self):
        """Does not return proxy segments."""
        init_segments({})
        targets = get_target_segments()
        codes = {s.code for s in targets}

        assert 'revesby_houses' not in codes
        assert 'wollstonecraft_units' not in codes


class TestIsInSegment:
    """Test segment membership checking."""

    def test_revesby_in_revesby_houses(self):
        """Revesby house is in revesby_houses."""
        init_segments({})
        assert is_in_segment('Revesby', 'house', 'revesby_houses') is True

    def test_revesby_unit_not_in_revesby_houses(self):
        """Revesby unit is not in revesby_houses."""
        init_segments({})
        assert is_in_segment('Revesby', 'unit', 'revesby_houses') is False

    def test_invalid_segment_returns_false(self):
        """Invalid segment code returns False."""
        assert is_in_segment('Revesby', 'house', 'invalid') is False


class TestGetAllTrackedSuburbs:
    """Test suburb collection."""

    def test_includes_all_suburbs(self):
        """Returns all tracked suburbs."""
        init_segments({})  # Use defaults
        suburbs = get_all_tracked_suburbs()

        assert 'revesby' in suburbs
        assert 'wollstonecraft' in suburbs
        assert 'lane cove' in suburbs
        assert 'chatswood' in suburbs
        assert 'lane cove north' in suburbs
        assert 'chatswood west' in suburbs


class TestGetOutpacingPairs:
    """Test outpacing pair generation."""

    def test_with_config(self):
        """Uses config for pairs when available."""
        config = {
            'gap_tracker': {
                'proxy_segments': ['revesby_houses', 'wollstonecraft_units'],
                'target_segment': 'lane_cove_houses',
                'secondary_target': 'chatswood_houses',
            }
        }
        pairs = get_outpacing_pairs(config)

        # Should have proxy → target and proxy → secondary pairs
        assert ('revesby_houses', 'lane_cove_houses') in pairs
        assert ('revesby_houses', 'chatswood_houses') in pairs
        assert ('wollstonecraft_units', 'lane_cove_houses') in pairs
        assert ('wollstonecraft_units', 'chatswood_houses') in pairs

    def test_without_config_uses_fallback(self):
        """Falls back to proxy vs target matching."""
        init_segments({})  # Use defaults (2 proxy, 2 target - all houses)
        pairs = get_outpacing_pairs()

        # Default segments have house proxies and house targets
        # So should match by same property type
        assert ('revesby_houses', 'lane_cove_houses') in pairs
        assert ('revesby_houses', 'chatswood_houses') in pairs
