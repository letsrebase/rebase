"""A talent's level as the hub reads it (REB-799): the years and the seniority of the
anonymous card, and what an admin said instead.

The prompt (`cards.py`) defines the bands, and `banded` is the parser's half of the same
rule: Claude is a poor judge of «mid or senior», and the audit found 39 «lead» cards out
of 101, six of them with two to five years. So the seniority of a card is the band its
`anni` falls in, `lead` being the one word a band alone cannot give: it needs `LEAD_FROM`
years *and* Claude's reading that the CV shows leadership (the prompt's test). A card
whose `anni` is `None` (the CV dates no role) keeps Claude's reading of the responsibility
alone, never `lead`, and `card_read` says it is uncertain.

An admin's correction lives on `freelancers` (`seniority_admin`, `anni_admin`), not on
the card, so a regenerated or retired card does not erase it; `card_read` applies it
wherever a card is read, and `Level` is the same answer for a reader that has the card's
JSON only (the talent list).
"""

from collections.abc import Mapping
from typing import Any, NamedTuple

from rebase_core.team_schemas import Card, CardRead

# Below `MID_FROM` years junior, from there to the year before `SENIOR_FROM` mid, from
# `SENIOR_FROM` senior; `lead` is senior with `LEAD_FROM` years and a leadership the CV
# states. The prompt says the same numbers in words (`cards._SYSTEM`), and the test of
# the prompt holds the two together.
MID_FROM = 3
SENIOR_FROM = 6
LEAD_FROM = 8


def seniority_for(anni: int) -> str:
    """The band `anni` years fall in: `junior`, `mid` or `senior`, never `lead`."""
    if anni < MID_FROM:
        return "junior"
    return "mid" if anni < SENIOR_FROM else "senior"


def banded(card: Card) -> Card:
    """`card` with its seniority inside the bands. With years, the seniority is the
    band's, unless Claude said `lead` and the years reach `LEAD_FROM`; without years,
    what Claude read from the responsibility, except `lead`, which nothing dated can back,
    read as `senior`. The anni and the rest are Claude's own."""
    if card.anni is None:
        return card.model_copy(update={"seniority": "senior"}) if card.seniority == "lead" else card
    if card.seniority == "lead" and card.anni >= LEAD_FROM:
        return card
    band = seniority_for(card.anni)
    return card if card.seniority == band else card.model_copy(update={"seniority": band})


class Level(NamedTuple):
    """What a reader shows of a talent's level: the effective `seniority` and `anni`
    (`None` when nobody can say), and `incerta` when the seniority is a guess."""

    seniority: str | None
    anni: int | None
    incerta: bool


def level(stored: Mapping[str, Any], seniority_admin: str | None, anni_admin: int | None) -> Level:
    """The level of a card as stored (its JSON, tolerant of a key that is missing or of
    the wrong type) under an admin's values: the admin's `seniority` and `anni` when
    there are any, the card's otherwise. Uncertain when the card has no years and no
    admin corrected the seniority."""
    anni = stored.get("anni")
    card_anni = anni if isinstance(anni, int) and not isinstance(anni, bool) else None
    seniority = stored.get("seniority")
    card_seniority = seniority if isinstance(seniority, str) else None
    return Level(
        seniority=seniority_admin or card_seniority,
        anni=anni_admin if anni_admin is not None else card_anni,
        incerta=seniority_admin is None and card_anni is None and card_seniority is not None,
    )


def card_read(stored: Any, seniority_admin: str | None, anni_admin: int | None) -> CardRead:
    """The stored card, validated, with the admin's level over it. Raises Pydantic's
    `ValidationError` like `Card.model_validate`: a stored card that is not a card is
    the caller's to leave out."""
    card = Card.model_validate(stored)
    effective = level(card.model_dump(), seniority_admin, anni_admin)
    return CardRead.model_validate(
        {
            **card.model_dump(),
            "seniority": effective.seniority,
            "anni": effective.anni,
            "seniority_incerta": effective.incerta,
        }
    )
