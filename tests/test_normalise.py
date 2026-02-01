# tests/test_normalise.py
import pytest
from tracker.ingest.normalise import (
    normalise_address,
    normalise_unit,
    normalise_house_number,
    normalise_street,
    normalise_suburb,
)


class TestNormaliseAddress:
    """Test the main normalise_address function."""

    def test_basic_address(self):
        """Standard address normalises correctly."""
        result = normalise_address(
            unit_number=None,
            house_number='11',
            street_name='Alliance Avenue',
            suburb='Revesby',
            postcode='2212'
        )
        assert result == '|11|alliance ave|revesby|2212'

    def test_unit_separate_field(self):
        """Unit number from separate field."""
        result = normalise_address(
            unit_number='2',
            house_number='10',
            street_name='Smith Street',
            suburb='Revesby',
            postcode='2212'
        )
        assert result == '2|10|smith st|revesby|2212'

    def test_unit_in_house_number(self):
        """Unit number extracted from house number field (2/10 format)."""
        result = normalise_address(
            unit_number=None,
            house_number='2/10',
            street_name='Smith Street',
            suburb='Revesby',
            postcode='2212'
        )
        assert result == '2|10|smith st|revesby|2212'

    def test_unit_with_prefix(self):
        """Unit number with 'Unit' prefix."""
        result = normalise_address(
            unit_number='Unit 5',
            house_number='20',
            street_name='Bay Road',
            suburb='Wollstonecraft',
            postcode='2065'
        )
        assert result == '5|20|bay rd|wollstonecraft|2065'

    def test_house_range(self):
        """House number range normalised."""
        result = normalise_address(
            unit_number=None,
            house_number='10-12',
            street_name='Main Street',
            suburb='Lane Cove',
            postcode='2066'
        )
        assert result == '|10-12|main st|lane cove|2066'

    def test_street_abbreviation_road(self):
        """Road abbreviated to rd."""
        result = normalise_address(
            unit_number=None,
            house_number='5',
            street_name='Smith Road',
            suburb='Chatswood',
            postcode='2067'
        )
        assert result == '|5|smith rd|chatswood|2067'

    def test_street_abbreviation_crescent(self):
        """Crescent abbreviated to cres."""
        result = normalise_address(
            unit_number=None,
            house_number='8',
            street_name='Milner Crescent',
            suburb='Wollstonecraft',
            postcode='2065'
        )
        assert result == '|8|milner cres|wollstonecraft|2065'

    def test_suburb_case_insensitive(self):
        """Suburb is lowercase."""
        result = normalise_address(
            unit_number=None,
            house_number='1',
            street_name='Test St',
            suburb='LANE COVE NORTH',
            postcode='2066'
        )
        assert result == '|1|test st|lane cove north|2066'


class TestNormaliseUnit:
    """Test unit number extraction and normalisation."""

    def test_simple_number(self):
        assert normalise_unit('2', '10') == '2'

    def test_unit_prefix(self):
        assert normalise_unit('Unit 3', '20') == '3'

    def test_apartment_prefix(self):
        assert normalise_unit('Apartment 5A', '100') == '5a'

    def test_apt_prefix(self):
        assert normalise_unit('Apt 7', '50') == '7'

    def test_slash_in_house(self):
        """Extract from 2/10 format in house number."""
        assert normalise_unit(None, '2/10') == '2'

    def test_slash_with_range(self):
        """Extract from 2/10-12 format."""
        assert normalise_unit(None, '3/10-12') == '3'

    def test_alphanumeric_unit(self):
        assert normalise_unit('5B', '20') == '5b'

    def test_none_unit(self):
        assert normalise_unit(None, '10') == ''


class TestNormaliseHouseNumber:
    """Test house number normalisation."""

    def test_simple_number(self):
        assert normalise_house_number('10') == '10'

    def test_alphanumeric(self):
        assert normalise_house_number('10A') == '10a'

    def test_range_hyphen(self):
        assert normalise_house_number('10-12') == '10-12'

    def test_range_en_dash(self):
        """En-dash should become hyphen."""
        assert normalise_house_number('10–12') == '10-12'

    def test_unit_slash_extracts_house(self):
        """Extract house from unit/house format."""
        assert normalise_house_number('2/10') == '10'

    def test_unit_slash_with_range(self):
        assert normalise_house_number('3/10-12') == '10-12'


class TestNormaliseStreet:
    """Test street name normalisation."""

    def test_street_to_st(self):
        assert normalise_street('Smith Street') == 'smith st'

    def test_road_to_rd(self):
        assert normalise_street('Main Road') == 'main rd'

    def test_avenue_to_ave(self):
        assert normalise_street('Collins Avenue') == 'collins ave'

    def test_av_to_ave(self):
        assert normalise_street('Park Av') == 'park ave'

    def test_crescent_to_cres(self):
        assert normalise_street('Bay Crescent') == 'bay cres'

    def test_place_to_pl(self):
        assert normalise_street('Oak Place') == 'oak pl'

    def test_lane_to_ln(self):
        assert normalise_street('Short Lane') == 'short ln'

    def test_drive_to_dr(self):
        assert normalise_street('Long Drive') == 'long dr'

    def test_already_abbreviated(self):
        assert normalise_street('Smith St') == 'smith st'

    def test_removes_punctuation(self):
        assert normalise_street("O'Connell Street") == 'oconnell st'

    def test_normalises_whitespace(self):
        assert normalise_street('  Main   Street  ') == 'main st'


class TestNormaliseSuburb:
    """Test suburb normalisation."""

    def test_lowercase(self):
        assert normalise_suburb('REVESBY') == 'revesby'

    def test_mixed_case(self):
        assert normalise_suburb('Lane Cove') == 'lane cove'

    def test_whitespace(self):
        assert normalise_suburb('  Chatswood  ') == 'chatswood'

    def test_known_variant(self):
        """Known suburb should map to canonical form."""
        assert normalise_suburb('lane cove north') == 'lane cove north'

    def test_heights_variant(self):
        assert normalise_suburb('Revesby Heights') == 'revesby heights'
