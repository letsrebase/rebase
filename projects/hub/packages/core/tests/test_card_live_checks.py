"""REB-1128: the rules of `cards-eval` on every live card. A card the writer keeps is read
against its CV and the hub's facts, with no model, and the fields its sources do not support
or contradict are stored beside it with a sentence saying why; the admin's card read and the
MCP's `read` give them back. The rules themselves are in `test_card_eval_checks.py`."""

import time
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fakes_cards import CARD, card_response, text_pdf
from freelancer_fixtures import apply_freelancer, card_writer, stored_card
from sqlalchemy.orm import Session

from rebase_core.card_draft import Conflict
from rebase_core.card_eval_checks import parse
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.card_live_checks import (
    MAX_YEARS_CHARS,
    CardFlag,
    card_flags,
    flags_json,
    split_stored,
)
from rebase_core.card_writer import CardWriter
from rebase_core.hub_facts import HubFacts, MatchFact
from rebase_core.llm import RecordingCall
from rebase_core.sources import SourceItem, SourceSet
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
    cv = "Backend developer, Python (2019), FastAPI, PostgreSQL, AWS, Acme 2015 - 2025"
    assert _flags(_card(experience_years=2), cv) == []


def test_a_field_that_names_the_person_is_flagged() -> None:
    [leak] = _flags(_card(role="Lovelace backend developer", experience_years=10), DATED_CV)

    assert (leak.field, leak.verdict) == ("role", "contradicted")


# ---- skills --------------------------------------------------------------------------------


def _skills(skills: list[str], cv: str = DATED_CV, **sources: Any) -> list[CardFlag]:
    return [
        flag
        for flag in card_flags(
            _card(skills=skills, experience_years=10), cv, NO_FACTS, KNOWN, TODAY, **sources
        )
        if flag.field == "skills"
    ]


def test_a_skill_a_source_spells_another_way_is_supported() -> None:
    cv = "Dev: JS, TS, Postgres, k8s, React.js, Node, Golang, Python 3.11, Vue.js, Next-JS."
    skills = [
        "JavaScript",
        "TypeScript",
        "PostgreSQL",
        "Kubernetes",
        "React",
        "Node.js",
        "Go",
        "Python",
        "VueJS",
        "Next JS",
    ]

    assert _skills(skills, cv) == []
    assert _skills(["Python 3", "Vue.js", "Node.js", "Next.js"], cv) == []


@pytest.mark.parametrize(
    ("skill", "cv"),
    [
        ("ES6", "Modern JavaScript (ES6+)"),
        ("HTML", "HTML5, CSS3"),
        ("CSS", "HTML5, CSS3"),
        ("Python", "Python3 scripting"),
        ("C++", "Embedded C++17"),
        ("Qt", "Desktop apps with Qt5"),
        ("Java", "Java8 services"),
        ("Vue.js", "Frontends in Vue3"),
        ("PHP", "Legacy PHP7"),
        ("Bootstrap", "Bootstrap4 themes"),
        ("Angular", "Angular2+ apps"),
        ("Angular", "Maintained AngularJS code"),
        ("AngularJS", "Angular apps"),
        ("Microservices", "microservizi in produzione"),
        ("Microservices", "a microservice architecture"),
        ("Microservice", "microservices"),
        ("Docker", "Dockers and Compose"),
        ("REST API", "Designed REST APIs"),
        ("Unit Test", "wrote unit tests"),
        ("Web Service", "SOAP web services"),
        ("Kubernetes", "Kubernetes operators"),
        ("S3", "AWS S3 buckets"),
    ],
)
def test_a_skill_the_cv_spells_with_a_version_a_plus_or_a_plural_is_supported(
    skill: str, cv: str
) -> None:
    assert _skills([skill], cv) == []


def test_a_version_or_a_plural_does_not_make_a_skill_a_mention_of_another() -> None:
    assert [flag.reason for flag in _skills(["Java"], "JavaScript5, JavaScripts")] == [
        "Nessuna fonte cita Java"
    ]
    assert _skills(["S3"], "Python3") != []


def test_a_skill_no_source_mentions_is_unsupported_and_named() -> None:
    [flag] = _skills(["Python", "Rust", "Terraform"])

    assert (flag.field, flag.verdict) == ("skills", "unsupported")
    assert flag.reason == "Nessuna fonte cita queste competenze: Rust, Terraform"
    [one] = _skills(["Rust"])
    assert one.reason == "Nessuna fonte cita Rust"


def test_a_skill_is_a_word_not_a_part_of_one() -> None:
    # `go` in «going» and `java` in «javascript» are no mention.
    assert [flag.reason for flag in _skills(["Go", "Java"], "Always going: JavaScript")] == [
        "Nessuna fonte cita queste competenze: Go, Java"
    ]


def test_a_skill_only_a_github_or_link_text_mentions_is_supported() -> None:
    github = SourceItem(
        "github",
        "adadev",
        1,
        "FONTE: GitHub\nRepository:\n- infra: Terraform modules",
        "a" * 64,
        "ok",
        NOW,
    )
    link = SourceItem("link", "https://ada.example", 1, "Ada, Rust and Go", "b" * 64, "ok", NOW)
    unread = SourceItem("link", "https://x.example", 2, "Elixir", "c" * 64, "fetch_failed", NOW)

    assert _skills(["Terraform"], sources=None) != []
    assert _skills(["Terraform", "Rust"], sources=SourceSet([github, link])) == []
    assert _skills(["Elixir"], sources=SourceSet([unread])) != []


def test_the_hub_facts_and_the_position_are_sources_of_a_skill() -> None:
    facts = HubFacts(
        seniority_admin=None,
        experience_years_admin=None,
        matches=[MatchFact("Backend Elixir developer", 6, "completed", "2025-01")],
        completed_months=6,
    )

    flags = card_flags(
        _card(skills=["Elixir", "Svelte"], experience_years=10),
        DATED_CV,
        facts,
        KNOWN,
        TODAY,
        job_title="Svelte developer",
    )

    assert [flag.field for flag in flags if flag.field == "skills"] == []


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


# ---- the CV's dates are read in time -------------------------------------------------------


@pytest.mark.parametrize(
    "cv",
    [
        "03/2019 - " * 5_000,  # fifty thousand characters, one line, five thousand ranges
        "Acme 2015 - 2019 Python\n" * 20_000,  # four hundred and eighty thousand characters
    ],
)
def test_a_crafted_cv_is_read_in_seconds(cv: str) -> None:
    started = time.process_time()
    card_flags(_card(experience_years=3), cv, NO_FACTS, KNOWN, TODAY)
    parse(cv, TODAY)

    assert time.process_time() - started < 2


def test_a_cv_past_the_cap_is_not_read_for_its_years() -> None:
    cv = "Backend developer, Acme, 2015 - 2025. " + "Python, FastAPI, PostgreSQL, AWS. " * 2_000

    assert len(cv) > MAX_YEARS_CHARS
    assert _flags(_card(experience_years=2), cv) == []
    assert _flags(_card(experience_years=2), cv[:1_000]) != []


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
