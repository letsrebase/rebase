"""REB-1128: the rules of `cards-eval` on every live card. A card the writer keeps is read
against its CV and the hub's facts, with no model, and the fields its sources do not support
or contradict are stored beside it with a sentence saying why; the admin's card read and the
MCP's `read` give them back. The rules themselves are in `test_card_eval_checks.py`."""

from datetime import UTC, date, datetime
from typing import Any

from fakes_cards import CARD, card_response, text_pdf
from freelancer_fixtures import apply_freelancer, card_writer, stored_card
from sqlalchemy.orm import Session

from rebase_core.card_draft import Conflict
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.card_live_checks import CardFlag, card_flags, flags_json, split_stored
from rebase_core.card_writer import CardWriter
from rebase_core.hub_facts import HubFacts
from rebase_core.llm import RecordingCall
from rebase_core.team_schemas import Card

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 17)
NO_FACTS = HubFacts(seniority_admin=None, experience_years_admin=None)
KNOWN = KnownIdentifiers.empty("Lovelace", "Ada")
# Ten years of dated work, nothing else in it that could name the person.
DATED_CV = "Backend developer, Acme, 2015 - 2025. Python, FastAPI, PostgreSQL, AWS."
UNDATED_CV = "Backend developer per anni: Python, FastAPI, PostgreSQL, AWS."


def _card(**changes: Any) -> Card:
    return Card.model_validate({**CARD, **changes})


def _flags(card: Card, cv: str, facts: HubFacts = NO_FACTS) -> list[CardFlag]:
    return card_flags(card, cv, facts, KNOWN, TODAY)


# ---- the rules, turned into flags ----------------------------------------------------------


def test_years_the_cv_dates_do_not_give_are_contradicted_and_the_seniority_follows() -> None:
    found = _flags(_card(experience_years=2, seniority="junior"), DATED_CV)

    assert [(flag.field, flag.verdict) for flag in found] == [
        ("experience_years", "contradicted"),
        ("seniority", "unsupported"),
    ]
    assert found[0].reason == "La scheda dichiara 2 anni, le date del CV ne danno da 10 a 11"


def test_a_card_with_no_years_where_the_cv_dates_a_career_is_contradicted() -> None:
    [years, _] = _flags(_card(experience_years=None, seniority="senior"), DATED_CV)

    assert years.verdict == "contradicted"
    assert years.reason == (
        "Il CV ha periodi professionali datati (da 10 a 11 anni), la scheda non indica gli anni"
    )


def test_years_on_a_cv_with_no_dates_are_unsupported() -> None:
    [years, _] = _flags(_card(experience_years=9), UNDATED_CV)

    assert (years.field, years.verdict) == ("experience_years", "unsupported")
    assert years.reason == "Il CV non riporta date, ma la scheda dichiara 9 anni"


def test_a_card_the_dates_support_has_no_flag() -> None:
    assert _flags(_card(experience_years=10, seniority="senior"), DATED_CV) == []
    assert _flags(_card(experience_years=None), UNDATED_CV) == []


def test_an_admins_years_are_a_fact_the_card_is_held_to() -> None:
    facts = HubFacts(seniority_admin=None, experience_years_admin=3)

    [years, _] = _flags(_card(experience_years=10), DATED_CV, facts)

    assert (years.verdict, years.reason) == (
        "contradicted",
        "Gli anni della scheda non sono quelli indicati dall'admin",
    )


def test_a_cv_the_rules_cannot_parse_reliably_is_left_to_the_judge() -> None:
    # A year written outside any period (`Python (2019)`) is not read, so nothing is flagged.
    cv = "Backend developer, Python (2019), Acme 2015 - 2025"
    assert _flags(_card(experience_years=2), cv) == []


def test_a_field_that_names_the_person_is_flagged() -> None:
    [leak] = _flags(_card(role="Lovelace backend developer", experience_years=10), DATED_CV)

    assert (leak.field, leak.verdict) == ("role", "contradicted")


def test_the_stored_list_keeps_conflicts_and_flags_apart() -> None:
    flag = CardFlag("experience_years", "contradicted", "perché")
    stored = [
        {
            "kind": "different_experience_years",
            "experience_years_card": 2,
            "experience_years_hub": 5,
        },
        *flags_json([flag]),
    ]

    assert split_stored(stored) == ([Conflict("different_experience_years", 2, 5)], [flag])
    assert split_stored(None) == ([], [])


# ---- on the live path ----------------------------------------------------------------------


def _write(session: Session, cv: str, card: dict[str, Any]) -> tuple[CardWriter, Any]:
    person = apply_freelancer(session, cv=text_pdf(cv))
    writer = card_writer(session, RecordingCall([card_response(card)]), now=NOW)
    writer.write(person)
    return writer, person


def test_a_written_card_is_stored_with_its_flags_and_read_back(clean_hub: Session) -> None:
    writer, person = _write(clean_hub, DATED_CV, {**CARD, "experience_years": 2})

    stored = stored_card(clean_hub, person)
    assert stored is not None
    assert stored.conflicts == [
        {
            "kind": "contradicted",
            "field": "experience_years",
            "reason": "La scheda dichiara 2 anni, le date del CV ne danno da 10 a 11",
        },
        {
            "kind": "unsupported",
            "field": "seniority",
            "reason": "Segue gli anni, che le fonti non confermano",
        },
    ]
    read = writer.read(person)
    assert [(flag.field, flag.verdict) for flag in read.flags] == [
        ("experience_years", "contradicted"),
        ("seniority", "unsupported"),
    ]
    admin = writer.read_admin(person)
    assert admin.flags == read.flags
    # The flags are not conflicts: the provenance keeps its own list.
    assert admin.provenance is not None and admin.provenance.conflicts == []


def test_a_card_the_dates_support_is_stored_with_no_flag(clean_hub: Session) -> None:
    writer, person = _write(clean_hub, DATED_CV, {**CARD, "experience_years": 10})

    stored = stored_card(clean_hub, person)
    assert stored is not None and stored.conflicts == []
    assert writer.read(person).flags == []


def test_a_person_with_no_card_has_no_flags(clean_hub: Session) -> None:
    person = apply_freelancer(clean_hub)

    assert CardWriter(clean_hub, None).read(person).flags == []


def test_a_regenerated_card_replaces_the_flags_of_the_one_before(clean_hub: Session) -> None:
    writer, person = _write(clean_hub, DATED_CV, {**CARD, "experience_years": 2})
    assert writer.read(person).flags

    answer = card_response({**CARD, "experience_years": 10})
    again = card_writer(clean_hub, RecordingCall([answer]), now=NOW)
    again.write(person, force=True)

    assert again.read(person).flags == []
