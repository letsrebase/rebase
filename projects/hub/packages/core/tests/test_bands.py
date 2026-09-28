"""The client's price band (REB-511, spec § 3.3): the freelancer's rate plus 40%,
placed in one of six bands of euro per day, and the team's band as the sum of its
people's, per day and per month at 22 days. Pure arithmetic, no database."""

from decimal import Decimal

import pytest

from rebase_core.bands import (
    BANDS,
    CLIENT_MARKUP,
    DAYS_PER_MONTH,
    Band,
    band_containing,
    band_for,
    team_bands,
)


def test_the_constants_are_the_specs() -> None:
    assert Decimal("1.4") == CLIENT_MARKUP
    assert DAYS_PER_MONTH == 22
    assert BANDS == ((0, 300), (300, 400), (400, 500), (500, 650), (650, 800), (800, None))


@pytest.mark.parametrize(
    ("price", "band"),
    [
        ("0", (0, 300)),
        ("299.99", (0, 300)),
        ("300", (300, 400)),  # a bound belongs to the band it opens: [300, 400)
        ("399.99", (300, 400)),
        ("400", (400, 500)),
        ("499.99", (400, 500)),
        ("500", (500, 650)),
        ("649.99", (500, 650)),
        ("650", (650, 800)),
        ("799.99", (650, 800)),
        ("800", (800, None)),
        ("5000", (800, None)),
    ],
)
def test_bands_at_every_boundary(price: str, band: tuple[int, int | None]) -> None:
    low, high = band
    assert band_containing(Decimal(price)) == Band(min=low, max=high)


@pytest.mark.parametrize(
    ("rate", "band"),
    [
        # 214.28 x 1.4 = 299.992: under 300. 214.29 x 1.4 = 300.006: 300 or more.
        ("214.28", (0, 300)),
        ("214.29", (300, 400)),
        ("250.00", (300, 400)),  # 350
        ("450.00", (500, 650)),  # 630
        ("571.43", (800, None)),  # 800.002
        ("571.42", (650, 800)),  # 799.988
    ],
)
def test_band_for_adds_forty_percent_to_the_rate(rate: str, band: tuple[int, int | None]) -> None:
    low, high = band
    assert band_for(Decimal(rate)) == Band(min=low, max=high)


def test_no_rate_has_no_band() -> None:
    assert band_for(None) is None


NBSP = "\u00a0"  # before «€», as the web's `formatEuro` writes it


def test_labels_are_the_bounds_in_euro() -> None:
    assert Band(min=400, max=500).bounds() == "400–500"
    assert Band(min=400, max=500).label() == f"400–500{NBSP}€ al giorno"
    assert Band(min=800, max=None).bounds() == "oltre 800"
    assert Band(min=800, max=None).label() == f"oltre 800{NBSP}€ al giorno"
    # A team's band per month runs into thousands, written the Italian way.
    assert Band(min=17600, max=23100).label("mese") == f"17.600–23.100{NBSP}€ al mese"
    assert " €" not in Band(min=400, max=500).label()  # never a breaking space


def test_the_lowest_band_says_only_its_top() -> None:
    assert Band(min=0, max=300).bounds() == "fino a 300"
    assert Band(min=0, max=300).label() == f"fino a 300{NBSP}€ al giorno"
    day, month = team_bands([Band(min=0, max=300)])
    assert day is not None and month is not None
    assert month.label("mese") == f"fino a 6.600{NBSP}€ al mese"


def test_team_bands_are_the_sum_per_day_and_per_month_at_22_days() -> None:
    day, month = team_bands([Band(min=300, max=400), Band(min=500, max=650)])
    assert day == Band(min=800, max=1050)
    assert month == Band(min=800 * 22, max=1050 * 22)


def test_team_bands_with_an_open_band_are_open() -> None:
    day, month = team_bands([Band(min=400, max=500), Band(min=800, max=None)])
    assert day == Band(min=1200, max=None)
    assert month == Band(min=1200 * 22, max=None)


def test_team_bands_with_a_missing_rate() -> None:
    assert team_bands([Band(min=400, max=500), None]) == (None, None)


def test_an_empty_team_has_no_band() -> None:
    assert team_bands([]) == (None, None)
