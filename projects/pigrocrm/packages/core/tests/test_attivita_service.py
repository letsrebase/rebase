"""`AttivitaService`: the commitment, its three states, and the date that may be absent.

Most of this file defends one decision of the spec (2026-09-03 §3.2): **`scadenza` is
nullable, and an undated activity is not late.** Everything downstream -- the calendar's
«senza scadenza» section, the `scade_entro` filter, the counts -- is only correct while
that holds, so it is asserted from several sides rather than once.
"""

from datetime import date
from uuid import UUID, uuid4

import pytest
from orologio import OGGI_IN_ITALIA, congela
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.attivita.schemas import AttivitaCreate, AttivitaListQuery, AttivitaUpdate
from pigrocrm.core.attivita.service import AttivitaService
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="user", role="readonly")


@pytest.fixture
def service(db_session: Session) -> AttivitaService:
    return AttivitaService(db_session)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    row = Customer(ragione_sociale=f"Cliente {uuid4()}")
    db_session.add(row)
    db_session.flush()
    return row.id


def _create(service: AttivitaService, **kwargs: object) -> object:
    payload: dict[str, object] = {"titolo": "Chiedere il codice SDI"}
    payload.update(kwargs)
    return service.create(AttivitaCreate(**payload), ADMIN)  # type: ignore[arg-type]


# --- what a commitment is --------------------------------------------------------------


def test_an_activity_can_have_no_date_at_all(service: AttivitaService) -> None:
    """The commonest to-do. A required date would produce either an invented one --
    which poisons every «what is due» list -- or no record at all."""
    created = _create(service)

    assert created.scadenza is None
    assert created.stato == "aperta"
    assert created.completata_il is None
    assert created.origine == "manuale"


def test_it_is_created_open_and_manual_whatever_the_caller_says(
    service: AttivitaService,
) -> None:
    """`stato` and `origine` are not fields of `AttivitaCreate` at all: a caller that
    could set them could claim to be an automation, and `origine` exists to answer
    exactly that question. Pydantic's default is to ignore an undeclared key rather than
    refuse it, so what is asserted is the *outcome* -- the row is open and manual
    whatever arrived -- and not an exception this schema does not raise."""
    created = service.create(
        AttivitaCreate.model_validate(
            {"titolo": "Non sono un'automazione", "origine": "automazione", "stato": "completata"}
        ),
        ADMIN,
    )

    assert (created.origine, created.stato, created.regola) == ("manuale", "aperta", None)


def test_a_readonly_actor_cannot_create_one(service: AttivitaService) -> None:
    with pytest.raises(PermissionDenied):
        service.create(AttivitaCreate(titolo="Non mia"), READONLY)


# --- the one reference -----------------------------------------------------------------


def test_it_may_hang_from_one_entity(service: AttivitaService, customer_id: UUID) -> None:
    created = _create(service, customer_id=customer_id)
    assert created.customer_id == customer_id


def test_two_references_are_refused_by_the_service(
    service: AttivitaService, customer_id: UUID, seeded_deal_id: UUID
) -> None:
    """The database has the same check and is the line that holds under concurrency;
    this is the one that turns it into a sentence somebody can read."""
    with pytest.raises(ValidationFailed) as excinfo:
        _create(service, customer_id=customer_id, deal_id=seeded_deal_id)

    assert "un solo" in str(excinfo.value)


def test_two_references_are_refused_by_the_database_too(
    db_session: Session, customer_id: UUID, seeded_deal_id: UUID
) -> None:
    """Straight past the service, because a check constraint that is never exercised is
    a check constraint that might not exist."""
    db_session.add(
        Attivita(titolo="Due riferimenti", customer_id=customer_id, deal_id=seeded_deal_id)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_reference_that_does_not_exist_is_not_found(service: AttivitaService) -> None:
    with pytest.raises(NotFound):
        _create(service, deal_id=uuid4())


def test_an_activity_may_belong_to_nobody(service: AttivitaService) -> None:
    """«Do March's e-invoicing» belongs to no customer -- the spec's own example."""
    created = _create(service, titolo="Fare la fatturazione elettronica di marzo")

    assert (created.customer_id, created.person_id, created.deal_id, created.invoice_id) == (
        None,
        None,
        None,
        None,
    )


# --- the three states ------------------------------------------------------------------


def test_completing_records_the_day_it_was_done(
    service: AttivitaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    created = _create(service)

    done = service.complete(created.id, ADMIN)

    assert done.stato == "completata"
    # `today_local()`, which is the emitter's day and not the process's: an hour logged
    # at 23:30 in Rome is that day, not the next one in UTC.
    assert done.completata_il == OGGI_IN_ITALIA


def test_completing_twice_does_not_move_the_date(
    service: AttivitaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second click on a checkbox. The date is when the work happened, so it must not
    slide to today every time somebody presses it again."""
    congela(monkeypatch)
    created = _create(service)
    first = service.complete(created.id, ADMIN)
    again = service.complete(created.id, ADMIN)

    assert again.completata_il == first.completata_il


def test_cancelling_closes_it_without_deleting_it(service: AttivitaService) -> None:
    """The whole reason the third state exists: six months later the question is not
    «where did it go» but *why* it stopped mattering."""
    created = _create(service, titolo="Sollecitare Rossi")

    cancelled = service.cancel(created.id, ADMIN)

    assert cancelled.stato == "annullata"
    assert cancelled.completata_il is None
    # Still there, and still readable.
    assert service.get(created.id, ADMIN).titolo == "Sollecitare Rossi"


def test_a_cancelled_activity_is_not_completed_by_mistake(service: AttivitaService) -> None:
    created = _create(service)
    service.cancel(created.id, ADMIN)

    with pytest.raises(Conflict):
        service.complete(created.id, ADMIN)


def test_reopening_clears_the_completion_date(service: AttivitaService) -> None:
    """Otherwise the row would be open and also say when it was finished."""
    created = _create(service)
    service.complete(created.id, ADMIN)

    reopened = service.reopen(created.id, ADMIN)

    assert (reopened.stato, reopened.completata_il) == ("aperta", None)


def test_the_database_refuses_a_completion_date_on_an_open_activity(
    db_session: Session,
) -> None:
    """The equivalence check, from the side the service never takes."""
    db_session.add(Attivita(titolo="Incoerente", stato="aperta", completata_il=date(2026, 9, 9)))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_a_rule_on_a_manual_activity(db_session: Session) -> None:
    db_session.add(Attivita(titolo="Manuale con regola", origine="manuale", regola="sollecito_1"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- rewriting -------------------------------------------------------------------------


def test_a_date_can_be_added_and_then_removed(service: AttivitaService) -> None:
    """Removing it has to be possible: an activity whose date turns out to be wrong must
    not be stuck with it, and §3.2 refuses invented dates."""
    created = _create(service)

    with_date = service.update(created.id, AttivitaUpdate(scadenza=date(2026, 9, 18)), ADMIN)
    assert with_date.scadenza == date(2026, 9, 18)

    without = service.update(created.id, AttivitaUpdate(scadenza_da_rimuovere=True), ADMIN)
    assert without.scadenza is None


def test_setting_and_clearing_a_date_in_one_request_is_refused(
    service: AttivitaService,
) -> None:
    created = _create(service)

    with pytest.raises(ValidationFailed):
        service.update(
            created.id,
            AttivitaUpdate(scadenza=date(2026, 9, 18), scadenza_da_rimuovere=True),
            ADMIN,
        )


def test_moving_it_to_a_second_entity_is_refused_by_reading_the_result(
    service: AttivitaService, customer_id: UUID, seeded_deal_id: UUID
) -> None:
    """The check reads the row as it *would be*, not the patch: a request that adds a
    deal to an activity already on a customer supplies one reference and produces two."""
    created = _create(service, customer_id=customer_id)

    with pytest.raises(ValidationFailed):
        service.update(created.id, AttivitaUpdate(deal_id=seeded_deal_id), ADMIN)


def test_it_can_be_unhooked_from_every_entity(service: AttivitaService, customer_id: UUID) -> None:
    created = _create(service, customer_id=customer_id)

    detached = service.update(created.id, AttivitaUpdate(riferimento_da_rimuovere=True), ADMIN)

    assert detached.customer_id is None


def test_an_unknown_key_is_refused_rather_than_ignored(service: AttivitaService) -> None:
    with pytest.raises(ValueError):
        AttivitaUpdate(titol="typo")  # type: ignore[call-arg]


def test_assigning_to_a_disabled_user_is_refused(
    service: AttivitaService, db_session: Session
) -> None:
    """Work assigned to a disabled account is a commitment nobody will ever see."""
    user = User(
        email=f"spento-{uuid4()}@example.test",
        password_hash="x",
        nome="Spento",
        ruolo="collaboratore",
        attivo=False,
    )
    db_session.add(user)
    db_session.flush()

    with pytest.raises(Conflict):
        _create(service, assegnata_a=user.id)


# --- archiving, which is not cancelling ------------------------------------------------


def test_archiving_hides_it_and_restoring_brings_it_back(service: AttivitaService) -> None:
    created = _create(service)
    service.soft_delete(created.id, ADMIN)

    with pytest.raises(NotFound):
        service.get(created.id, ADMIN)

    restored = service.restore(created.id, ADMIN)
    assert restored.stato == "aperta"


# --- the list --------------------------------------------------------------------------


def test_a_date_filter_excludes_the_undated_ones(service: AttivitaService) -> None:
    """The property everything else rests on. `NULL <= date` is NULL, which is not true
    -- and a filter that quietly included them would answer «due by Friday» with things
    that are not due at all."""
    _create(service, titolo="Senza data")
    _create(service, titolo="Con data", scadenza=date(2026, 9, 18))

    page = service.list(AttivitaListQuery(scade_entro=date(2026, 9, 30)), ADMIN)

    assert [item.titolo for item in page.items] == ["Con data"]


def test_the_undated_ones_can_be_asked_for_explicitly(service: AttivitaService) -> None:
    _create(service, titolo="Senza data")
    _create(service, titolo="Con data", scadenza=date(2026, 9, 18))

    page = service.list(AttivitaListQuery(senza_scadenza=True), ADMIN)

    assert [item.titolo for item in page.items] == ["Senza data"]


def test_the_archived_ones_are_their_own_list(service: AttivitaService) -> None:
    """`archiviate` is the complement of the live list, never a union with it: a screen
    that offers «ripristina» has to be able to find what it restores, and the live list
    has to keep hiding it."""
    kept = _create(service, titolo="Resta")
    gone = _create(service, titolo="Per errore")
    service.soft_delete(gone.id, ADMIN)

    live = service.list(AttivitaListQuery(), ADMIN)
    archived = service.list(AttivitaListQuery(archiviate=True), ADMIN)

    assert [item.id for item in live.items] == [kept.id]
    assert [item.id for item in archived.items] == [gone.id]

    service.restore(gone.id, ADMIN)
    assert service.list(AttivitaListQuery(archiviate=True), ADMIN).items == []


def test_the_list_does_not_hide_what_was_completed(service: AttivitaService) -> None:
    """`stato` defaults to «every state»: a list that silently dropped the completed
    ones is a list somebody will read as «the work was never done»."""
    created = _create(service)
    service.complete(created.id, ADMIN)

    page = service.list(AttivitaListQuery(), ADMIN)

    assert [item.stato for item in page.items] == ["completata"]


def test_sorting_by_a_date_column_pages_without_a_broken_cursor(
    service: AttivitaService,
) -> None:
    """`scadenza` is the project's first `Date` sort key, which is why `SortKind` grew a
    `date` branch: encoded as text it would reach Postgres as `date > text`, an operator
    that does not exist, and the second page would fail at the database."""
    for day in (10, 11, 12):
        _create(service, titolo=f"Giorno {day}", scadenza=date(2026, 9, day))

    first = service.list(AttivitaListQuery(sort="scadenza", limit=2), ADMIN)
    assert [item.titolo for item in first.items] == ["Giorno 10", "Giorno 11"]
    assert first.next_cursor is not None

    second = service.list(
        AttivitaListQuery(sort="scadenza", limit=2, cursor=first.next_cursor), ADMIN
    )
    assert [item.titolo for item in second.items] == ["Giorno 12"]


def test_the_timeline_records_what_happened_to_it(
    service: AttivitaService, db_session: Session
) -> None:
    """Three closures of the same row are three different facts, and the timeline is
    where «why» lives."""
    created = _create(service)
    service.complete(created.id, ADMIN)
    service.reopen(created.id, ADMIN)
    service.cancel(created.id, ADMIN)

    kinds = db_session.execute(
        text(
            "SELECT kind FROM activities WHERE entity_type = 'attivita' AND entity_id = :id "
            "ORDER BY occurred_at, id"
        ),
        {"id": str(created.id)},
    ).scalars()

    assert list(kinds) == ["created", "completata", "riaperta", "annullata"]
