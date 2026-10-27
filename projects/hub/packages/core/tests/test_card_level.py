"""The card's level (REB-799): the year bands the parser enforces, and the admin's own
seniority and years over Claude's. Pure functions, no database."""

from typing import Any

import pytest
from fakes_cards import CARD

from rebase_core.card_level import (
    LEAD_FROM,
    MID_FROM,
    SENIOR_FROM,
    Level,
    banded,
    card_read,
    level,
    seniority_for,
)
from rebase_core.team_schemas import Card


def _card(seniority: str, anni: int | None) -> Card:
    return Card.model_validate({**CARD, "seniority": seniority, "anni": anni})


@pytest.mark.parametrize(
    ("anni", "band"),
    [
        (0, "junior"),
        (2, "junior"),
        (3, "mid"),
        (5, "mid"),
        (6, "senior"),
        (9, "senior"),
        (30, "senior"),
    ],
)
def test_the_bands_are_junior_under_three_mid_to_five_senior_from_six(anni: int, band: str) -> None:
    assert (MID_FROM, SENIOR_FROM, LEAD_FROM) == (3, 6, 8)
    assert seniority_for(anni) == band


@pytest.mark.parametrize(
    ("said", "anni", "written"),
    [
        # What the audit found: lead with two to five years, senior with three, mid with
        # seven, junior with ten, all brought inside the bands.
        ("lead", 2, "junior"),
        ("lead", 4, "mid"),
        ("lead", 5, "mid"),
        ("lead", 7, "senior"),
        ("senior", 3, "mid"),
        ("mid", 7, "senior"),
        ("junior", 10, "senior"),
        ("mid", 0, "junior"),
        # Already inside a band: untouched.
        ("junior", 1, "junior"),
        ("mid", 4, "mid"),
        ("senior", 6, "senior"),
        # `lead` stays for Claude's leadership reading, and only from eight years.
        ("lead", 8, "lead"),
        ("lead", 25, "lead"),
        # Senior at eight years and more is not lead without Claude saying so.
        ("senior", 12, "senior"),
        # A CV that dates nothing: Claude's reading of the responsibility stands,
        # never `lead`, which nothing dated backs.
        ("junior", None, "junior"),
        ("mid", None, "mid"),
        ("senior", None, "senior"),
        ("lead", None, "senior"),
    ],
)
def test_the_parser_brings_a_seniority_inside_the_year_bands(
    said: str, anni: int | None, written: str
) -> None:
    result = banded(_card(said, anni))

    assert (result.seniority, result.anni) == (written, anni)
    assert result.model_dump(exclude={"seniority"}) == _card(said, anni).model_dump(
        exclude={"seniority"}
    )


def test_a_card_without_years_is_a_card() -> None:
    """`anni` is required in the schema but may be null: a CV that dates no role."""
    card = Card.model_validate({**CARD, "anni": None})
    assert card.anni is None
    with pytest.raises(ValueError, match="anni"):
        Card.model_validate({k: v for k, v in CARD.items() if k != "anni"})
    with pytest.raises(ValueError, match="anni"):
        Card.model_validate({**CARD, "anni": -1})
    with pytest.raises(ValueError, match="anni"):
        Card.model_validate({**CARD, "anni": 61})


@pytest.mark.parametrize(
    ("stored", "seniority_admin", "anni_admin", "expected"),
    [
        # Nobody corrected it: the card's own.
        ({"seniority": "senior", "anni": 9}, None, None, Level("senior", 9, False)),
        # The admin's seniority and years beat the card's, each on its own.
        ({"seniority": "senior", "anni": 9}, "junior", None, Level("junior", 9, False)),
        ({"seniority": "senior", "anni": 9}, None, 12, Level("senior", 12, False)),
        ({"seniority": "senior", "anni": 9}, "lead", 0, Level("lead", 0, False)),
        # Years of 0 are an answer, not a missing one.
        ({"seniority": "junior", "anni": 5}, None, 0, Level("junior", 0, False)),
        # No dated role: uncertain, until the admin says the seniority.
        ({"seniority": "mid", "anni": None}, None, None, Level("mid", None, True)),
        ({"seniority": "mid", "anni": None}, None, 4, Level("mid", 4, True)),
        ({"seniority": "mid", "anni": None}, "senior", None, Level("senior", None, False)),
        # A card the JSON of which lacks or mistypes a key does not break a reader.
        ({"seniority": "mid"}, None, None, Level("mid", None, True)),
        ({"seniority": 3, "anni": "9"}, None, None, Level(None, None, False)),
        ({"anni": True}, None, None, Level(None, None, False)),
        ({}, None, 7, Level(None, 7, False)),
    ],
)
def test_the_admins_level_beats_the_cards_each_field_on_its_own(
    stored: dict[str, Any],
    seniority_admin: str | None,
    anni_admin: int | None,
    expected: Level,
) -> None:
    assert level(stored, seniority_admin, anni_admin) == expected


def test_card_read_is_the_card_with_the_effective_level() -> None:
    plain = card_read(CARD, None, None)
    assert (plain.seniority, plain.anni, plain.seniority_incerta) == ("senior", 9, False)
    assert plain.model_dump(exclude={"seniority_incerta"}) == Card.model_validate(CARD).model_dump()

    corrected = card_read({**CARD, "anni": None, "seniority": "mid"}, "lead", 11)
    assert (corrected.seniority, corrected.anni, corrected.seniority_incerta) == ("lead", 11, False)
    assert corrected.ruolo == CARD["ruolo"] and corrected.sintesi == CARD["sintesi"]

    undated = card_read({**CARD, "anni": None}, None, None)
    assert (undated.anni, undated.seniority_incerta) == (None, True)

    with pytest.raises(ValueError, match="ruolo"):
        card_read({"seniority": "mid", "anni": 3}, "lead", 3)
