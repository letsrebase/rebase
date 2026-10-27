"""The hub's own facts about a freelancer, as a source of their card (REB-838)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.errors import NotFound
from rebase_core.hub_facts import HubFacts, hub_facts
from rebase_core.models import AdminAction, Comment, Company, Freelancer, Match, User

TODAY = date(2026, 10, 27)
CLIENT = "Initech Italia srl"


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in ("admin_actions", "comments", "matches", "companies", "freelancers", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _user(session: Session, email: str, **extra: Any) -> User:
    user = User(email=email, nome="Ada", cognome="Lovelace", **extra)
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
    stato: str = "concluso",
    inizio: date | None = date(2025, 1, 1),
    fine: date | None = date(2025, 6, 30),
    closed: datetime | None = None,
    figura: str = "Backend developer",
    budget: str = "777",
    nome_azienda: str = CLIENT,
) -> Match:
    admin = session.query(User).filter(User.email == "admin@rebase.it").first() or _user(
        session, "admin@rebase.it", role="admin"
    )
    company = Company(
        user_id=admin.id,
        nome_azienda=nome_azienda,
        figura_richiesta=figura,
        progetto="Il progetto segreto del cliente",
        periodo_da=date(2025, 1, 1),
        durata="6 mesi",
        budget_giornaliero=Decimal(budget),
        remoto="remoto",
        numero_risorse=1,
        note="nota riservata dell'admin",
    )
    session.add(company)
    session.flush()
    match = Match(
        freelancer_id=person.id,
        company_id=company.id,
        cliente_ragione_sociale=CLIENT,
        cliente_piva="01234567890",
        cliente_sede="Via Segreta 1, Milano",
        stato=stato,
        created_by=admin.id,
        lettera_data_inizio=inizio,
        lettera_data_fine=fine,
        lettera_compenso=Decimal("777"),
        updated_at=closed or datetime(2025, 7, 1, tzinfo=UTC),
    )
    session.add(match)
    session.flush()
    return match


def _facts(session: Session, person: Freelancer) -> HubFacts:
    return hub_facts(session, person.id, TODAY)


def test_client_name_never_in_text(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, figura="Backend developer per Initech Italia srl")
    _match(clean, person, figura="Data engineer presso INITECH", inizio=date(2024, 1, 1))

    facts = _facts(clean, person)
    for material in (facts.text(), facts.hash_material()):
        for secret in ("Initech", "INITECH", "srl", "777", "segreto", "01234567890", "Segreta"):
            assert secret not in material
    assert "Backend developer per il cliente" in facts.text()
    assert "Data engineer presso il cliente" in facts.text()


def test_admin_override_present_as_hard_fact(clean: Session) -> None:
    person = _person(clean, seniority_admin="lead", anni_admin=12)

    facts = _facts(clean, person)

    assert (facts.seniority_admin, facts.anni_admin) == ("lead", 12)
    assert "lead (dato certo)" in facts.text()
    assert "12 (dato certo)" in facts.text()
    assert "FONTE: dati della hub" in facts.text()


def test_only_dated_matches_counted_in_completed_months(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, inizio=date(2025, 1, 1), fine=date(2025, 3, 31))  # 3 months
    _match(clean, person, inizio=date(2025, 6, 1), fine=date(2025, 6, 30))  # 1 month
    _match(clean, person, inizio=None, fine=None)  # undated: left out
    _match(clean, person, stato="bozza")  # never started
    _match(clean, person, stato="annullato")
    _match(clean, person, stato="in_firma")

    facts = _facts(clean, person)

    assert [(m.dal, m.mesi, m.esito) for m in facts.matches] == [
        ("2025-01", 3, "completato"),
        ("2025-06", 1, "completato"),
    ]
    assert facts.completed_months == 4


def test_overlapping_completed_matches_count_their_months_once(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, inizio=date(2025, 1, 1), fine=date(2025, 6, 30))
    _match(
        clean,
        person,
        inizio=date(2025, 4, 1),
        fine=date(2025, 9, 30),
        closed=datetime(2025, 10, 1, tzinfo=UTC),
    )

    assert _facts(clean, person).completed_months == 9


def test_outcomes(clean: Session) -> None:
    person = _person(clean)
    _match(
        clean,
        person,
        inizio=date(2025, 1, 1),
        fine=date(2025, 12, 31),
        closed=datetime(2025, 4, 1, tzinfo=UTC),
    )  # closed early: 3 months, not completed
    _match(clean, person, stato="attivo", inizio=date(2026, 8, 1), fine=date(2027, 1, 31))

    facts = _facts(clean, person)

    assert [(m.mesi, m.esito) for m in facts.matches] == [(3, "chiuso prima"), (3, "in corso")]
    assert facts.completed_months == 0


def test_in_progress_months_do_not_move_the_hash(clean: Session) -> None:
    person = _person(clean)
    _match(clean, person, stato="attivo", inizio=date(2026, 8, 1), fine=None)

    before = hub_facts(clean, person.id, date(2026, 10, 27))
    later = hub_facts(clean, person.id, date(2026, 11, 27))

    assert before.matches[0].mesi != later.matches[0].mesi
    assert before.hash_material() == later.hash_material()
    assert before.text() != later.text()


def test_admin_comments_never_included(clean: Session) -> None:
    person = _person(clean, note="nota riservata sulla persona")
    _match(clean, person)
    clean.add(
        Comment(
            entity_type="freelancer", entity_id=person.id, testo="Commento privato", autore="Ivan"
        )
    )
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
    _match(clean, person, figura="Backend developer")

    facts = _facts(clean, person)

    assert facts.clients == [CLIENT]
    assert CLIENT not in facts.text() and CLIENT not in facts.hash_material()


def test_the_close_date_is_the_trails_not_the_last_edit(clean: Session) -> None:
    """`updated_at` moves with any later edit of the match: the months, the outcome and the
    completed months would move with it, and the cards with them."""
    person = _person(clean)
    match = _match(
        clean, person, inizio=date(2025, 1, 1), fine=None, closed=datetime(2026, 3, 10, tzinfo=UTC)
    )
    clean.add(
        AdminAction(
            entity_type="match",
            entity_id=match.id,
            kind="match_closed",
            admin_id=_user(clean, "boss@rebase.it", role="admin").id,
            payload={},
            created_at=datetime(2025, 7, 1, tzinfo=UTC),
        )
    )
    clean.flush()

    facts = _facts(clean, person)

    assert [(m.mesi, m.esito) for m in facts.matches] == [(6, "completato")]
    assert facts.completed_months == 6
