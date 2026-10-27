"""REB-842: the checks of an evaluated card that need no model: the CV's dated periods and
the years recomputed from them, when the parse is reliable, and the anonymity."""

from datetime import date
from typing import Any

import pytest
from fakes_cards import CARD

from rebase_core.card_eval_checks import (
    YearsCheck,
    anonymity_leaks,
    check_years,
    parse,
    years_of,
)
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.team_schemas import Card

TODAY = date(2026, 10, 27)


def _card(**changes: Any) -> Card:
    return Card.model_validate({**CARD, **changes})


def _years(text: str) -> int:
    return years_of(parse(text, TODAY).periods)


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("Backend developer, Acme, 2018 - 2022", 5),
        ("Backend developer, Acme, 2018–2022", 5),
        ("Backend developer, Acme, 2015–19", 5),
        ("dal 2015 al 2019 sviluppatore", 5),
        ("03/2015 - 06/2019 sviluppatore", 4),
        ("01/03/2015 - 31/12/2018 sviluppatore", 3),
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
    parsed = parse(text, TODAY)

    assert years_of(parsed.periods) == years
    assert parsed.complete


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


@pytest.mark.parametrize(
    "text",
    [
        "Dev Acme 2019 -\nDev Foo 2015 - 2017",  # an open period the rule did not read
        "Dev Acme dal 2016\nDev Foo 2015 - 2017",
        "Python (2019), Dev Foo 2015 - 2017",  # a year that is no period
        "Dev Foo 2015 - 2017, nata nel 1990",
    ],
)
def test_a_year_the_parse_did_not_read_makes_it_incomplete(text: str) -> None:
    assert parse(text, TODAY).complete is False


def test_a_study_beside_the_dates_marks_the_period() -> None:
    text = (
        "Laurea in Informatica, Università di Torino\n2012 - 2017\nSviluppatore, Acme 2018 - 2022"
    )

    assert [p.study for p in parse(text, TODAY).periods] == [True, False]
    assert parse("B.Sc. Informatica 2010 - 2014", TODAY).periods[0].study is True


def _check(card_years: int | None, text: str, **hub: Any) -> YearsCheck:
    return check_years(_card(anni=card_years), text, today=TODAY, **hub)


def test_a_reliable_parse_recomputes_the_years() -> None:
    cv = "Sviluppatore backend, Acme, 2017 - 2025"

    # A bare year is somewhere in the year: 8 years at the least, 9 at the most.
    assert _check(9, cv) == YearsCheck("recomputed", True, 8, 9)
    assert _check(8, cv).ok is True
    assert _check(10, cv).ok is True  # a year over: the CV's months are not known
    assert _check(5, cv).ok is False
    assert _check(12, cv).ok is False


def test_a_study_in_the_parse_makes_it_the_judges_unless_it_does_not_matter() -> None:
    cv = "Laurea, Università di Torino 2012 - 2017\n\nEsperienza\nSviluppatore, Acme, 2018 - 2020"

    # Nine years with the study, two without: which was work is not for a rule to say.
    assert _check(6, cv) == YearsCheck("judged", None, 2, 9)
    assert _check(12, cv).ok is None


def test_a_study_on_the_line_next_to_a_period_makes_the_parse_ambiguous() -> None:
    cv = "Laurea, Università di Torino 2012 - 2017\nSviluppatore, Acme, 2018 - 2020"

    assert _check(6, cv) == YearsCheck("judged", None, 0, 9)


def test_jobs_written_with_bare_years_are_the_judges_when_the_count_could_be_double() -> None:
    cv = "Dev, Acme, 2019 - 2020\nDev, Foo, 2021 - 2022\nDev, Bar, 2023 - 2024"

    # Three years if each lasted a year, six if each lasted two.
    assert _check(3, cv) == YearsCheck("judged", None, 3, 6)


@pytest.mark.parametrize(
    "cv",
    [
        "Dev Acme 2015–19\nDev Foo 2019 - oggi",
        "Dev Acme 2015 - 2019\nDev Foo 2019 -",  # an open period: left unread, so judged
        "Dev Acme dal 2016\nDev Foo 2016 - 2018",
    ],
)
def test_a_period_the_rule_cannot_read_is_never_a_contradiction(cv: str) -> None:
    assert _check(11, cv).ok in (True, None)


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


def test_a_two_digit_number_that_wraps_the_century_is_no_period() -> None:
    parsed = parse("Dev Acme 2019 - 18 mesi in team", TODAY)

    assert parsed.periods == [] and parsed.complete is False
