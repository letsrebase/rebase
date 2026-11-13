"""The hub's own facts about a freelancer, as a source of their card (REB-838), read from the
hub's read model of the CRM's engagements (REB-1106)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from types import EllipsisType
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.card_provenance import sha
from rebase_core.errors import NotFound
from rebase_core.hub_facts import HubFacts, MatchFact, hub_facts
from rebase_core.models import Company, Freelancer, HubEngagement, Note, User

TODAY = date(2026, 10, 27)
CLIENT = "Initech Italia srl"


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in ("notes", "hub_engagements", "companies", "freelancers", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _user(session: Session, email: str, **extra: Any) -> User:
    user = User(email=email, first_name="Ada", last_name="Lovelace", **extra)
    session.add(user)
    session.flush()
    return user


def _person(session: Session, **extra: Any) -> Freelancer:
    row = Freelancer(user_id=_user(session, "ada@studio.it").id, links=[], **extra)
    session.add(row)
    session.flush()
    return row


def _match(
    session: Session,
    person: Freelancer,
    *,
    status: str = "concluded",
    start: date | None = date(2025, 1, 1),
    fine: date | None = date(2025, 6, 30),
    planned: date | None | EllipsisType = ...,
    closed: datetime | None = None,
    profile: str = "Backend developer",
    budget: str = "777",
    company_name: str = CLIENT,
) -> HubEngagement:
    """An engagement as the CRM's notices leave it: `fine` is its end (a closed one's actual
    day), `planned` the end it had while it was live (the end it was planned to have, `fine`
    unless said)."""
    admin = session.query(User).filter(User.email == "admin@rebase.it").first() or _user(
        session, "admin@rebase.it", role="admin"
    )
    company = Company(
        user_id=admin.id,
        company_name=company_name,
        requested_role="Il ruolo scritto dal cliente",
        project="Il progetto segreto del cliente",
        period_from=date(2025, 1, 1),
        duration="6 mesi",
        daily_budget=Decimal(budget),
        remote="remote",
        headcount=1,
    )
    session.add(company)
    session.flush()
    engagement = HubEngagement(
        freelancer_id=person.id,
        company_id=company.id,
        client_name=CLIENT,
        role=profile,
        mode="day",
        fee=Decimal("777"),
        state=status,
        sequence=1,
        starts_on=start,
        ends_on=fine,
        planned_ends_on=fine if planned is ... else planned,
        updated_at=closed or datetime(2025, 7, 1, tzinfo=UTC),
    )
    session.add(engagement)
    session.flush()
    return engagement


def _facts(session: Session, person: Freelancer) -> HubFacts:
    return hub_facts(session, person.id, TODAY)


def test_client_name_never_in_text(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, profile="Backend developer per Initech Italia srl")
    _match(clean, person, profile="Data engineer presso INITECH", start=date(2024, 1, 1))

    facts = _facts(clean, person)
    for material in (facts.text(), facts.hash_material()):
        for secret in ("Initech", "INITECH", "srl", "777", "segreto", "01234567890", "Segreta"):
            assert secret not in material
    assert "Backend developer per il cliente" in facts.text()
    assert "Data engineer presso il cliente" in facts.text()


def test_admin_override_present_as_hard_fact(clean: Session) -> None:
    person = _person(clean, seniority_admin="lead", experience_years_admin=12)

    facts = _facts(clean, person)

    assert (facts.seniority_admin, facts.experience_years_admin) == ("lead", 12)
    assert "lead (dato certo)" in facts.text()
    assert "12 (dato certo)" in facts.text()
    assert "FONTE: dati della hub" in facts.text()


def test_only_dated_matches_counted_in_completed_months(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, start=date(2025, 1, 1), fine=date(2025, 3, 31))  # 3 months
    _match(clean, person, start=date(2025, 6, 1), fine=date(2025, 6, 30))  # 1 month
    _match(clean, person, start=None, fine=None)  # undated: left out
    _match(clean, person, status="draft")  # never started
    _match(clean, person, status="cancelled")
    _match(clean, person, status="awaiting_signature")

    facts = _facts(clean, person)

    assert [(m.since, m.months, m.outcome) for m in facts.matches] == [
        ("2025-01", 3, "completed"),
        ("2025-06", 1, "completed"),
    ]
    assert facts.completed_months == 4


def test_overlapping_completed_matches_count_their_months_once(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, start=date(2025, 1, 1), fine=date(2025, 6, 30))
    _match(
        clean,
        person,
        start=date(2025, 4, 1),
        fine=date(2025, 9, 30),
        closed=datetime(2025, 10, 1, tzinfo=UTC),
    )

    assert _facts(clean, person).completed_months == 9


def test_outcomes(clean: Session) -> None:
    person = _person(clean)
    _match(
        clean,
        person,
        start=date(2025, 1, 1),
        fine=date(2025, 4, 1),
        planned=date(2025, 12, 31),
    )  # closed early, on 1 April: 3 months, not completed
    _match(clean, person, status="active", start=date(2026, 8, 1), fine=date(2027, 1, 31))

    facts = _facts(clean, person)

    assert [(m.months, m.outcome) for m in facts.matches] == [
        (3, "closed_before"),
        (3, "in_progress"),
    ]
    assert facts.completed_months == 0


def test_in_progress_months_do_not_move_the_hash(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, status="active", start=date(2026, 8, 1), fine=None)

    before = hub_facts(clean, person.id, date(2026, 10, 27))
    later = hub_facts(clean, person.id, date(2026, 11, 27))

    assert before.matches[0].months != later.matches[0].months
    assert before.hash_material() == later.hash_material()
    assert before.text() != later.text()


def test_admin_notes_never_included(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person)
    for text_ in ("Commento privato", "nota riservata sulla persona"):
        clean.add(Note(entity_type="freelancer", entity_id=person.id, text=text_, author="Ivan"))
    clean.flush()

    facts = _facts(clean, person)

    for material in (facts.text(), facts.hash_material()):
        for secret in ("Commento privato", "Ivan", "riservata"):
            assert secret not in material


def test_no_matches_gives_only_override(clean: Session) -> None:
    person = _person(clean, seniority_admin="senior")

    facts = _facts(clean, person)

    assert facts.matches == [] and facts.completed_months == 0
    assert facts.text().splitlines()[0] == "FONTE: dati della hub"
    assert "Incarichi" not in facts.text() and "senior (dato certo)" in facts.text()


def test_nothing_known_hashes_to_nothing(clean: Session) -> None:
    person = _person(clean)

    facts = _facts(clean, person)

    assert facts.hash_material() == ""
    assert "Nessun dato" in facts.text()


def test_unknown_freelancer_is_not_found(clean: Session) -> None:
    with pytest.raises(NotFound):
        hub_facts(clean, UUID(int=1), TODAY)


def test_client_names_are_kept_for_the_guard_and_in_no_text(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, profile="Backend developer")

    facts = _facts(clean, person)

    assert facts.clients == [CLIENT]
    assert CLIENT not in facts.text() and CLIENT not in facts.hash_material()


def test_the_close_date_is_the_rows_end_not_the_last_edit(clean: Session) -> None:
    """`updated_at` moves with any later notice of the engagement: the months, the outcome and
    the completed months would move with it, and the cards with them. A closed engagement ends
    on the day the CRM closed it, which is the row's `ends_on`."""
    person = _person(clean)
    _match(
        clean,
        person,
        start=date(2025, 1, 1),
        fine=date(2025, 7, 1),
        planned=None,
        closed=datetime(2026, 3, 10, tzinfo=UTC),
    )

    facts = _facts(clean, person)

    assert [(m.months, m.outcome) for m in facts.matches] == [(6, "completed")]
    assert facts.completed_months == 6


def _fixed_facts() -> HubFacts:
    return HubFacts(
        seniority_admin="senior",
        experience_years_admin=7,
        matches=[
            MatchFact(
                role="Backend per il cliente", months=14, outcome="completed", since="2024-01"
            ),
            MatchFact(role="Data engineer", months=3, outcome="in_progress", since="2026-08"),
            MatchFact(role="Frontend", months=2, outcome="closed_before", since="2025-05"),
        ],
        completed_months=14,
        clients=["Acme"],
    )


def test_the_hash_and_the_text_are_what_the_italian_code_made_of_them() -> None:
    """Every stored card keeps `source_hashes["hub"]`, so a byte of `hash_material()` that
    moves marks them all stale, and the block the model reads is the one it always read.
    Both are the values the code before the English rename produced for this fixture
    (`origin/main`: `Esito`, `ruolo`, `mesi`, `anni=`, `completati=`)."""
    facts = _fixed_facts()

    assert facts.hash_material() == (
        "seniority=senior\n"
        "anni=7\n"
        "2024-01|Backend per il cliente|completato|14\n"
        "2026-08|Data engineer|in corso|\n"
        "2025-05|Frontend|chiuso prima|2\n"
        "completati=14"
    )
    assert (
        sha(facts.hash_material())
        == "cbc7335923d4154c19b26bd3cf902cb7590c655c740a430dca9f9318468b0503"
    )
    assert facts.text() == (
        "FONTE: dati della hub\n"
        "Seniority stabilita da un admin: senior (dato certo)\n"
        "Anni di esperienza stabiliti da un admin: 7 (dato certo)\n"
        "Incarichi svolti tramite la hub (ruolo, inizio, durata, esito):\n"
        "- Backend per il cliente, dal 2024-01, 14 mesi, completato\n"
        "- Data engineer, dal 2026-08, 3 mesi, in corso\n"
        "- Frontend, dal 2025-05, 2 mesi, chiuso prima\n"
        "Mesi di incarichi completati: 14"
    )
