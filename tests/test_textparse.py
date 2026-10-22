"""Snippets below are verbatim (or near-verbatim) text seen on real listings in Oct 2026."""
from datetime import date

import pytest

from app import textparse as tp

TODAY = date(2026, 10, 22)


@pytest.mark.parametrize("unit,expected", [
    ("pppw", ("pw", True)), ("PPPW", ("pw", True)), ("p.p.p.w", ("pw", True)),
    ("per person per week", ("pw", True)), ("pcm", ("pcm", False)), ("pw", ("pw", False)),
    ("per month", ("pcm", False)), ("pppm", ("pcm", True)), ("/week", ("pw", False)),
    (None, (None, False)),
])
def test_classify_unit(unit, expected):
    assert tp.classify_unit(unit) == expected


def test_rightmove_bills_package_listing():
    text = ("£115PPPW - AVAILABLE AUGUST! Compulsory Additional £24PPPW Bills Package - NO USAGE CAP "
            "£115 PPPW / £2,998.50PCM")
    g = tp.guess_rent(text, bedrooms=6, structured_amount=2999, structured_period="pcm")
    assert g.pppw == 115
    assert g.bills_pppw == 24
    assert g.bills_included is False


def test_zoopla_pppw_with_inc_bills_variant():
    text = "£2,409 pcm(£555.92 pw) 4 beds £139pppw (£166pppw inc bills) | Four Double Bedrooms"
    g = tp.guess_rent(text, bedrooms=4)
    assert g.pppw == 139
    assert g.bills_pppw == 27
    assert g.bills_included is False


def test_unihomes_bills_included():
    text = "Bills Included 2 bathrooms 5 Bedroom House Rosebery Crescent, Jesmond £170 per person per week"
    g = tp.guess_rent(text, bedrooms=5)
    assert g.pppw == 170
    assert g.bills_included is True
    assert g.total_pcm == pytest.approx(170 * 52 / 12 * 5, rel=1e-3)


def test_whole_property_pcm_only():
    g = tp.guess_rent("3 bed house £1,100 pcm", bedrooms=3)
    assert g.total_pcm == 1100
    assert g.pppw == pytest.approx(1100 / (52 / 12) / 3, rel=1e-3)
    assert g.source == "text-whole"


def test_structured_whole_property_divided_by_people():
    g = tp.guess_rent("", bedrooms=5, people=4, structured_amount=2600, structured_period="pcm")
    assert g.total_pcm == 2600
    assert g.pppw == pytest.approx(2600 / (52 / 12) / 4, rel=1e-3)


def test_deposit_not_rent():
    ms = tp.extract_prices("Deposit: £2,999 pcm rent £2,999 pcm")
    assert ms[0].is_deposit and not ms[1].is_deposit


def test_spareroom_equivalent_per_room():
    ms = tp.extract_prices("£1,100 pcm 3 bed house - Available 9th Nov 2026 (Equivalent to £366 per room)")
    assert any(m.per_person and m.amount == 366 for m in ms)


@pytest.mark.parametrize("text,expected", [
    ("Bills included? / Yes", True),
    ("Bills Included", True),
    ("£179 pw - bills inc.", True),
    ("Compulsory Additional £24PPPW Bills Package", False),
    ("rent excluding bills", False),
    ("Lovely house near the park", None),
])
def test_detect_bills(text, expected):
    assert tp.detect_bills_included(text) is expected


@pytest.mark.parametrize("text,expected,now", [
    ("Let available date: 07/07/2027", date(2027, 7, 7), False),
    ("Available from 1 Aug 2027", date(2027, 8, 1), False),
    ("added today, available from 26th August 2027", date(2027, 8, 26), False),
    ("Available 9th Nov 2026", date(2026, 11, 9), False),
    ("Available now", TODAY, True),
    ("Available immediately", TODAY, True),
    ("Available From Today", TODAY, True),
    ("Available from 13 July 2027", date(2027, 7, 13), False),
    ("AVAILABLE AUGUST!", date(2027, 8, 1), False),
    ("Available July 2027", date(2027, 7, 1), False),
    ("Nice house", None, False),
])
def test_parse_available(text, expected, now):
    assert tp.parse_available(text, TODAY) == (expected, now)


def test_month_only_rolls_to_next_year():
    assert tp.parse_date_fragment("June", TODAY) == date(2027, 6, 1)
    assert tp.parse_date_fragment("November", TODAY) == date(2026, 11, 1)


@pytest.mark.parametrize("text,beds,baths", [
    ("4 beds / 2 baths / 1 reception", 4, 2),
    ("6 bedroom house BATHROOMS 2", 6, 2),
    ("five bedroom house with two bathrooms", 5, 2),
    ("5 Bedroom House 2 bathrooms", 5, 2),
    ("4 double bedrooms, 4 en-suites", 4, 4),
    ("2 Beds - 1 Bath - Furnished", 2, 1),
])
def test_rooms(text, beds, baths):
    assert tp.parse_bedrooms(text) == beds
    assert tp.parse_bathrooms(text) == baths


def test_postcodes():
    assert tp.parse_postcode("Valley View, Jesmond, Newcastle Upon Tyne NE2 1AB") == ("NE2 1AB", "NE2")
    assert tp.parse_postcode("2 Bed Flat, Peel House, NE1") == (None, "NE1")


def test_academic_year():
    assert tp.academic_year_hint("Available for 2027/28") == (2027, 2028)
    assert tp.academic_year_hint("2027-2028 academic year") == (2027, 2028)


def test_furnished():
    assert tp.parse_furnished("Furnish type: Furnished") == "furnished"
    assert tp.parse_furnished("Unfurnished") == "unfurnished"
