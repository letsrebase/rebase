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


def _card(seniority: str, experience_years: int | None) -> Card:
    return Card.model_validate(
        {**CARD, "seniority": seniority, "experience_years": experience_years}
    )


@pytest.mark.parametrize(
    ("experience_years", "band"),
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
def test_the_bands_are_junior_under_three_mid_to_five_senior_from_six(
    experience_years: int, band: str
) -> None:
    assert (MID_FROM, SENIOR_FROM, LEAD_FROM) == (3, 6, 8)
    assert seniority_for(experience_years) == band


@pytest.mark.parametrize(
    ("said", "experience_years", "written"),
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
    said: str, experience_years: int | None, written: str
) -> None:
    result = banded(_card(said, experience_years))

    assert (result.seniority, result.experience_years) == (written, experience_years)
    assert result.model_dump(exclude={"seniority"}) == _card(said, experience_years).model_dump(
        exclude={"seniority"}
    )


def test_a_card_without_years_is_a_card() -> None:
    """`experience_years` is required in the schema but may be null: a CV that dates no role."""
    card = Card.model_validate({**CARD, "experience_years": None})
    assert card.experience_years is None
    with pytest.raises(ValueError, match="experience_years"):
        Card.model_validate({k: v for k, v in CARD.items() if k != "experience_years"})
    with pytest.raises(ValueError, match="experience_years"):
        Card.model_validate({**CARD, "experience_years": -1})
    with pytest.raises(ValueError, match="experience_years"):
        Card.model_validate({**CARD, "experience_years": 61})


@pytest.mark.parametrize(
    ("stored", "seniority_admin", "experience_years_admin", "expected"),
    [
        # Nobody corrected it: the card's own.
        ({"seniority": "senior", "experience_years": 9}, None, None, Level("senior", 9, False)),
        # The admin's seniority and years beat the card's, each on its own.
        ({"seniority": "senior", "experience_years": 9}, "junior", None, Level("junior", 9, False)),
        ({"seniority": "senior", "experience_years": 9}, None, 12, Level("senior", 12, False)),
        ({"seniority": "senior", "experience_years": 9}, "lead", 0, Level("lead", 0, False)),
        # Years of 0 are an answer, not a missing one.
        ({"seniority": "junior", "experience_years": 5}, None, 0, Level("junior", 0, False)),
        # No dated role: uncertain, until the admin says the seniority.
        ({"seniority": "mid", "experience_years": None}, None, None, Level("mid", None, True)),
        ({"seniority": "mid", "experience_years": None}, None, 4, Level("mid", 4, True)),
        (
            {"seniority": "mid", "experience_years": None},
            "senior",
            None,
            Level("senior", None, False),
        ),
        # A card the JSON of which lacks or mistypes a key does not break a reader.
        ({"seniority": "mid"}, None, None, Level("mid", None, True)),
        ({"seniority": 3, "experience_years": "9"}, None, None, Level(None, None, False)),
        ({"experience_years": True}, None, None, Level(None, None, False)),
        ({}, None, 7, Level(None, 7, False)),
    ],
)
def test_the_admins_level_beats_the_cards_each_field_on_its_own(
    stored: dict[str, Any],
    seniority_admin: str | None,
    experience_years_admin: int | None,
    expected: Level,
) -> None:
    assert level(stored, seniority_admin, experience_years_admin) == expected


def test_card_read_is_the_card_with_the_effective_level() -> None:
    plain = card_read(CARD, None, None)
    assert (plain.seniority, plain.experience_years, plain.seniority_uncertain) == (
        "senior",
        9,
        False,
    )
    assert (
        plain.model_dump(exclude={"seniority_uncertain"}) == Card.model_validate(CARD).model_dump()
    )

    corrected = card_read({**CARD, "experience_years": None, "seniority": "mid"}, "lead", 11)
    assert (corrected.seniority, corrected.experience_years, corrected.seniority_uncertain) == (
        "lead",
        11,
        False,
    )
    assert corrected.role == CARD["role"] and corrected.profile_summary == CARD["profile_summary"]

    undated = card_read({**CARD, "experience_years": None}, None, None)
    assert (undated.experience_years, undated.seniority_uncertain) == (None, True)

    with pytest.raises(ValueError, match="role"):
        card_read({"seniority": "mid", "experience_years": 3}, "lead", 3)
