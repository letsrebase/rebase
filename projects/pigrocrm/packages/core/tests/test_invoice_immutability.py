"""Spec 4: an issued invoice is a fiscal document, not a CRM row with an extra state.

A correction is a new document, and which route is available is not the user's choice
-- it depends on a verifiable fact: whether the file has left for the intermediary.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from orologio import fissa_today
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import Conflict, ImmutableField, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import (
    InvoiceAnnul,
    InvoiceCreate,
    InvoiceIssue,
    InvoiceLineIn,
    InvoiceTransmitted,
    InvoiceUpdate,
    PaymentState,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=None, type="user", role="collaborator")
# `today_local()`, not `date.today()`: this test's own oracle must agree with the
# service's (see `db/clock.py` and Task 10's report -- this host runs JST, not
# Europe/Rome, so the two would silently diverge otherwise).
TODAY = today_local()


@pytest.fixture(autouse=True)
def _pinned_today(monkeypatch: pytest.MonkeyPatch) -> None:
    fissa_today(monkeypatch, TODAY)


@pytest.fixture
def service(db_session: Session, tmp_path) -> InvoiceService:  # type: ignore[no-untyped-def]
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            cap="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    return InvoiceService(db_session, LocalFileStorage(tmp_path / "documents"))


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        cap="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _issue(service: InvoiceService, customer_id: UUID) -> UUID:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            memo="Consulenza",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    return service.issue(draft.id, InvoiceIssue(), ADMIN).id


# --- annulment: the correction route for an invoice that never left ------------------


def test_annulling_keeps_the_number_and_the_row_readable(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The equivalent of a struck-through page in a paper register, and what preserves
    the gap-free property of spec 3: without it, "no gaps" would be worth nothing."""
    invoice_id = _issue(service, customer_id)
    annulled = service.annul(invoice_id, InvoiceAnnul(reason="importo errato"), ADMIN)
    assert annulled.status == "cancelled"
    assert annulled.number == 1
    assert annulled.cancelled_at == TODAY
    assert annulled.cancellation_reason == "importo errato"
    assert annulled.total == Decimal("1000.00")
    assert len(service.lines(invoice_id, ADMIN)) == 1


def test_a_corrected_invoice_takes_the_next_number_not_the_annulled_one(
    service: InvoiceService, customer_id: UUID
) -> None:
    first = _issue(service, customer_id)
    service.annul(first, InvoiceAnnul(reason="importo errato"), ADMIN)
    assert service.get(_issue(service, customer_id), ADMIN).number == 2


def test_an_annulment_needs_a_reason(service: InvoiceService, customer_id: UUID) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvoiceAnnul(reason="")


def test_a_draft_cannot_be_annulled(service: InvoiceService, customer_id: UUID) -> None:
    """It has no number to preserve, so it is deleted, not struck through."""
    draft = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    with pytest.raises(Conflict):
        service.annul(draft.id, InvoiceAnnul(reason="ripensamento"), ADMIN)


def test_annulling_twice_is_refused(service: InvoiceService, customer_id: UUID) -> None:
    invoice_id = _issue(service, customer_id)
    service.annul(invoice_id, InvoiceAnnul(reason="importo errato"), ADMIN)
    with pytest.raises(Conflict):
        service.annul(invoice_id, InvoiceAnnul(reason="ancora"), ADMIN)


def test_annulling_requires_admin(service: InvoiceService, customer_id: UUID) -> None:
    invoice_id = _issue(service, customer_id)
    with pytest.raises(PermissionDenied):
        service.annul(invoice_id, InvoiceAnnul(reason="importo errato"), COLLABORATOR)


def test_annulling_records_an_activity_with_the_reason(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    service.annul(invoice_id, InvoiceAnnul(reason="importo errato"), ADMIN)
    entries = ActivityService(db_session).timeline("invoice", invoice_id)
    annulled = [entry for entry in entries if entry.kind == "annulled"]
    assert annulled and annulled[0].payload["reason"] == "importo errato"


# --- transmission: the fact that decides which correction route exists --------------


def test_marking_transmitted_is_a_one_way_door(service: InvoiceService, customer_id: UUID) -> None:
    """Spec 4: settable once, then frozen. This column is the reason annulment is safe
    rather than optimistic -- without it the system could not tell an invoice that
    never left from one already deposited with the Agenzia delle Entrate."""
    invoice_id = _issue(service, customer_id)
    marked = service.mark_transmitted_externally(invoice_id, InvoiceTransmitted(data=TODAY), ADMIN)
    assert marked.transmitted_externally_at == TODAY
    with pytest.raises(ImmutableField) as caught:
        service.mark_transmitted_externally(invoice_id, InvoiceTransmitted(data=TODAY), ADMIN)
    assert caught.value.details["field"] == "transmitted_externally_at"


def test_a_transmitted_invoice_cannot_be_annulled(
    service: InvoiceService, customer_id: UUID
) -> None:
    """From here the correction needs a credit note, which this slice does not produce
    -- so it happens outside PigroCRM and the application says so, instead of offering
    a button that pretends to solve it."""
    invoice_id = _issue(service, customer_id)
    service.mark_transmitted_externally(invoice_id, InvoiceTransmitted(data=TODAY), ADMIN)
    with pytest.raises(Conflict) as caught:
        service.annul(invoice_id, InvoiceAnnul(reason="importo errato"), ADMIN)
    assert "nota di credito" in caught.value.message


def test_a_future_transmission_date_is_refused(service: InvoiceService, customer_id: UUID) -> None:
    invoice_id = _issue(service, customer_id)
    with pytest.raises(ValidationFailed) as caught:
        service.mark_transmitted_externally(
            invoice_id, InvoiceTransmitted(data=TODAY + timedelta(days=1)), ADMIN
        )
    assert caught.value.details["field"] == "transmitted_externally_at"


def test_a_transmission_date_before_the_issue_date_is_refused(
    service: InvoiceService, customer_id: UUID
) -> None:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("100.00"))],
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(issue_date=TODAY), ADMIN)
    with pytest.raises(ValidationFailed):
        service.mark_transmitted_externally(
            issued.id, InvoiceTransmitted(data=TODAY - timedelta(days=1)), ADMIN
        )


def test_a_draft_cannot_be_marked_transmitted(service: InvoiceService, customer_id: UUID) -> None:
    draft = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    with pytest.raises(Conflict):
        service.mark_transmitted_externally(draft.id, InvoiceTransmitted(data=TODAY), ADMIN)


def test_marking_transmitted_requires_admin(service: InvoiceService, customer_id: UUID) -> None:
    invoice_id = _issue(service, customer_id)
    with pytest.raises(PermissionDenied):
        service.mark_transmitted_externally(
            invoice_id, InvoiceTransmitted(data=TODAY), COLLABORATOR
        )


# --- what stays mutable, and what the database refuses ------------------------------


def test_collection_stays_mutable_on_an_annulled_invoice_is_refused_but_on_an_issued_one_is_not(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Collection is a subsequent fact on a live invoice; on a struck-through one
    there is nothing to collect."""
    live = _issue(service, customer_id)
    assert (
        service.set_payment_state(
            live, PaymentState(payment_status="paid", payment_date=TODAY), ADMIN
        ).payment_status
        == "paid"
    )
    annulled = _issue(service, customer_id)
    service.annul(annulled, InvoiceAnnul(reason="importo errato"), ADMIN)
    with pytest.raises(Conflict):
        service.set_payment_state(
            annulled, PaymentState(payment_status="paid", payment_date=TODAY), ADMIN
        )


def test_the_internal_notes_and_custom_fields_stay_mutable_forever(
    service: InvoiceService, customer_id: UUID
) -> None:
    """They appear on no artefact, so they are not part of the document."""
    invoice_id = _issue(service, customer_id)
    service.annul(invoice_id, InvoiceAnnul(reason="importo errato"), ADMIN)
    assert (
        service.update(
            invoice_id, InvoiceUpdate(note_interne="sostituita da 2/2026"), ADMIN
        ).note_interne
        == "sostituita da 2/2026"
    )


def test_the_soft_delete_of_an_issued_invoice_fails_in_raw_sql_too(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Spec 14.4 is explicit that the database must enforce this, not the service:
    an invariant only the service defends is one a psql session walks past."""
    invoice_id = _issue(service, customer_id)
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE invoices SET deleted_at = now() WHERE id = :id"), {"id": invoice_id}
        )
    db_session.rollback()


def test_an_annulled_invoice_still_cannot_be_soft_deleted(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    service.annul(invoice_id, InvoiceAnnul(reason="importo errato"), ADMIN)
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE invoices SET deleted_at = now() WHERE id = :id"), {"id": invoice_id}
        )
    db_session.rollback()
