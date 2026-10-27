"""REB-842: the checks of an evaluated card that need no model: the CV's dated periods and
the years recomputed from them, the seniority's band and the anonymity."""

from datetime import date
from typing import Any

import pytest
from fakes_cards import CARD

from rebase_core.card_eval_checks import (
    Period,
    YearsCheck,
    anonymity_leaks,
    check_years,
    periods,
    seniority_in_band,
    years_of,
)
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.team_schemas import Card

TODAY = date(2026, 10, 27)


def _card(**changes: Any) -> Card:
    return Card.model_validate({**CARD, **changes})


def _years(text: str) -> int:
    return years_of(periods(text, TODAY))


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("Backend developer, Acme, 2018 - 2022", 5),
        ("Backend developer, Acme, 2018–2022", 5),
        ("dal 2015 al 2019 sviluppatore", 5),
        ("03/2015 - 06/2019 sviluppatore", 4),
        ("Gennaio 2015 - Dicembre 2019", 5),
        ("Jan 2015 to Mar 2018", 3),
        ("Sett 2015 - Giu 2018", 2),
        ("2020 - oggi", 6),
        ("Marzo 2019 – Presente", 7),
        ("2021 - Present", 5),
        ("2019 - ongoing", 7),
    ],
)
def test_the_periods_a_cv_writes_are_counted_in_whole_years(text: str, years: int) -> None:
    assert _years(text) == years


def test_overlapping_periods_count_once_and_gaps_are_left_out() -> None:
    text = "2015 - 2018 Acme\n2017 - 2019 Globex\n2022 - 2023 Initech"
    # 2015-2019 is five years, 2022-2023 two.
    assert _years(text) == 7


@pytest.mark.parametrize(
    "text",
    [
        "Stage 03/2020 - 04/2020",  # an internship of two months
        "Python 2030 - 2031",  # starts in the future
        "2019 - 2015",  # ends before it starts
        "Telefono 345 1234567",  # no period at all
        "versione 1.2019",  # a number, not a range
    ],
)
def test_what_is_not_a_professional_period_is_not_counted(text: str) -> None:
    assert _years(text) == 0


def test_a_study_beside_the_dates_marks_the_period() -> None:
    text = (
        "Laurea in Informatica, Università di Torino\n2012 - 2017\nSviluppatore, Acme 2018 - 2022"
    )
    found = periods(text, TODAY)

    assert [(p.study) for p in found] == [True, False]
    assert isinstance(found[0], Period)


def _check(card_years: int | None, text: str, **hub: Any) -> YearsCheck:
    return check_years(_card(anni=card_years), text, today=TODAY, **hub)


def test_a_reliable_parse_recomputes_the_years() -> None:
    cv = "Sviluppatore backend, Acme, 2017 - 2025"

    assert _check(9, cv) == YearsCheck("recomputed", True, 9, 9)
    assert _check(8, cv).ok is True  # the card may be a year under: the CV's months are counted
    assert _check(5, cv).ok is False
    assert _check(2, cv).ok is False


def test_more_years_than_every_period_adds_up_to_is_a_contradiction_whatever_the_parse() -> None:
    cv = "Laurea, Università di Torino 2012 - 2017\n\nEsperienza\nSviluppatore, Acme, 2018 - 2020"

    # The study makes the parse ambiguous (9 years with it, 3 without), but 12 is impossible.
    assert _check(12, cv).ok is False
    assert _check(6, cv) == YearsCheck("judged", None, 3, 9)


def test_a_study_on_the_line_next_to_a_period_makes_the_parse_ambiguous() -> None:
    cv = "Laurea, Università di Torino 2012 - 2017\nSviluppatore, Acme, 2018 - 2020"

    assert _check(6, cv) == YearsCheck("judged", None, 0, 9)


def test_a_cv_with_no_year_at_all_dates_nothing_so_the_card_has_no_years() -> None:
    cv = "Backend developer a Torino da nove anni: Python, FastAPI, AWS."

    assert _check(None, cv) == YearsCheck("undated", True)
    assert _check(9, cv) == YearsCheck("undated", False)
    # The hub's completed engagements are dated work.
    assert _check(2, cv, hub_completed_months=26).ok is True
    assert _check(4, cv, hub_completed_months=26).ok is False


def test_a_year_that_is_no_period_is_left_to_the_judge() -> None:
    assert _check(3, "Python (2019), progetti open source").mode == "judged"


def test_a_card_without_years_where_the_cv_dates_a_career_is_a_mismatch() -> None:
    assert _check(None, "Sviluppatore, Acme, 2015 - 2025").ok is False


def test_an_admins_years_are_a_fact() -> None:
    cv = "Sviluppatore, Acme, 2015 - 2025"

    assert _check(5, cv, anni_admin=5) == YearsCheck("admin", True)
    assert _check(6, cv, anni_admin=5).ok is True
    assert _check(9, cv, anni_admin=5).ok is False
    assert _check(None, cv, anni_admin=5).ok is False


def test_the_hub_adds_its_completed_years_to_what_the_cv_allows() -> None:
    cv = "Sviluppatore, Acme, 2023 - 2025"

    assert _check(5, cv).ok is False
    assert _check(5, cv, hub_completed_months=36).ok is True


@pytest.mark.parametrize(
    ("seniority", "anni", "in_band"),
    [
        ("junior", 2, True),
        ("mid", 4, True),
        ("senior", 6, True),
        ("lead", 9, True),
        ("senior", 2, False),
        ("lead", 5, False),
        ("mid", None, True),
        ("lead", None, False),
    ],
)
def test_the_seniority_is_the_band_the_years_fall_in(
    seniority: str, anni: int | None, in_band: bool
) -> None:
    assert seniority_in_band(_card(seniority=seniority, anni=anni)) is in_band


def test_anonymity_is_read_against_everything_the_person_holds() -> None:
    known = KnownIdentifiers(
        cognome="Lovelace",
        nome="Ada",
        github_login="adal",
        repo_names=["analytical-engine"],
        hostnames=["lovelace.dev"],
    )

    assert anonymity_leaks(_card(), known) == []
    assert anonymity_leaks(
        _card(sintesi="Ha scritto analytical-engine, una libreria per i calcoli."), known
    ) == ["sintesi"]
    assert anonymity_leaks(_card(ruolo="Lovelace backend developer"), known) == ["ruolo"]
