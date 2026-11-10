"""Closing an IVA period against writes to its registers (REB-1036, suppliers design § 6).

The rule, in one place: a period a person closed refuses every purchase and sales-document write
dated inside it, with a sentence that names the period, until it is reopened; closing and
reopening are an unscoped administrator's and each leaves a timeline entry. What the registers add
up to is `test_iva_settlement.py`'s and `test_iva_register.py`'s.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from frozen_clock import freeze_today
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.errors import AgentForbidden, Conflict, NotFound, PermissionDenied
from pigrocrm.core.fiscal.iva_lock import IvaPeriodLockService, lock_id
from pigrocrm.core.fiscal.iva_register import IvaRegisterService
from pigrocrm.core.fiscal.models import IvaPeriodLock
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert, IvaPeriodLockCreate
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import (
    InvoiceAnnul,
    InvoiceCreate,
    InvoiceImport,
    InvoiceIssue,
    InvoiceLineIn,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.schemas import OrganizationCreate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.purchases.schemas import PurchaseCreate, PurchaseUpdate, VatLine
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.models import CostCategory

SYSTEM = Actor(id=None, type="system", role="admin")
D = date
TODAY = D(2026, 11, 10)


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first three quarters of 2026 have ended, the fourth has not."""
    freeze_today(monkeypatch, TODAY)


@pytest.fixture
def admin(db_session: Session) -> Actor:
    user = User(
        email=f"admin-{uuid4()}@example.test", password_hash="x", name="Giulia", role="admin"
    )
    db_session.add(user)
    db_session.flush()
    return Actor(id=user.id, type="user", role="admin")


@pytest.fixture
def entity(db_session: Session) -> UUID:
    """The default legal entity on the ordinary pack, settling by quarter."""
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        SYSTEM,
    )
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="it-standard", iva_frequency="quarterly"), SYSTEM
    )
    return LegalEntityService(db_session).resolve().id


@pytest.fixture
def invoices(db_session: Session, entity: UUID, tmp_path: Path) -> InvoiceService:
    return InvoiceService(db_session, LocalFileStorage(tmp_path / "documents"))


@pytest.fixture
def category(db_session: Session) -> UUID:
    row = CostCategory(name=f"Servizi {uuid4()}", position=0, code=None)
    db_session.add(row)
    db_session.flush()
    return row.id


def _supplier(session: Session) -> UUID:
    return (
        OrganizationService(session)
        .create(OrganizationCreate(legal_name="Vendor Srl", roles=["supplier"]), SYSTEM)
        .id
    )


def _customer(session: Session) -> UUID:
    customer = Organization(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    session.add(customer)
    session.flush()
    return customer.id


def _purchase(
    service: PurchaseService, category: UUID, supplier: UUID, day: date, **overrides: object
):  # noqa: ANN202
    payload: dict[str, object] = {
        "kind": "invoice",
        "supplier_id": supplier,
        "number": "FA/12",
        "category_id": category,
        "date": day,
        "amount": Decimal("122.00"),
        "description": "Consulenza",
        "vat_lines": [
            VatLine(
                rate=Decimal("22.00"), taxable_amount=Decimal("100.00"), vat_amount=Decimal("22.00")
            )
        ],
    }
    payload.update(overrides)
    return service.create(PurchaseCreate(**payload), SYSTEM)  # type: ignore[arg-type]


def _close(session: Session, admin: Actor, entity: UUID, number: int = 1, year: int = 2026):  # noqa: ANN202
    return IvaPeriodLockService(session).close_iva_period(
        IvaPeriodLockCreate(year=year, number=number, legal_entity_id=entity), admin
    )


# --- closing and reopening ------------------------------------------------------------------


def test_closing_settles_the_period_locks_it_and_leaves_a_timeline_entry(
    db_session: Session, entity: UUID, admin: Actor
) -> None:
    lock = _close(db_session, admin, entity)

    assert (lock.frequency, lock.year, lock.number) == ("quarterly", 2026, 1)
    assert (lock.period_start, lock.period_end) == (D(2026, 1, 1), D(2026, 3, 31))
    assert lock.label == "1° trimestre 2026"
    assert lock.closed_by == admin.id
    assert lock.id == lock_id(entity, "quarterly", 2026, 1)
    period = IvaRegisterService(db_session).list_periods(2026, admin, legal_entity_id=entity)[0]
    assert (period.closed, period.closed_by, period.settled) == (True, admin.id, True)
    assert period.closed_at == lock.closed_at
    assert not any(
        other.closed
        for other in IvaRegisterService(db_session).list_periods(
            2026, admin, legal_entity_id=entity
        )[1:]
    )
    kinds = [row.kind for row in ActivityService(db_session).timeline("iva_period_lock", lock.id)]
    assert kinds == ["iva_period_closed"]


def test_reopening_deletes_the_lock_and_one_timeline_reads_both_acts(
    db_session: Session, entity: UUID, admin: Actor
) -> None:
    lock = _close(db_session, admin, entity)
    IvaPeriodLockService(db_session).reopen_iva_period(2026, 1, admin, legal_entity_id=entity)
    again = _close(db_session, admin, entity)

    assert again.id == lock.id
    assert len(db_session.scalars(select(IvaPeriodLock)).all()) == 1
    kinds = [row.kind for row in ActivityService(db_session).timeline("iva_period_lock", lock.id)]
    assert sorted(kinds) == ["iva_period_closed", "iva_period_closed", "iva_period_reopened"]
    IvaPeriodLockService(db_session).reopen_iva_period(2026, 1, admin, legal_entity_id=entity)
    assert db_session.scalars(select(IvaPeriodLock)).all() == []


def test_a_period_that_has_not_ended_cannot_be_closed(
    db_session: Session, entity: UUID, admin: Actor
) -> None:
    with pytest.raises(Exception) as refused:  # noqa: PT011 - `ValidationFailed`, checked below
        _close(db_session, admin, entity, number=4)
    assert type(refused.value).__name__ == "ValidationFailed"
    assert db_session.scalars(select(IvaPeriodLock)).all() == []


def test_a_period_closes_once_and_reopening_one_that_is_open_is_not_found(
    db_session: Session, entity: UUID, admin: Actor
) -> None:
    _close(db_session, admin, entity)
    with pytest.raises(Conflict) as twice:
        _close(db_session, admin, entity)
    assert "1° trimestre 2026" in str(twice.value.details["reason"])
    with pytest.raises(NotFound):
        IvaPeriodLockService(db_session).reopen_iva_period(2026, 2, admin, legal_entity_id=entity)


def test_only_an_unscoped_administrator_closes_or_reopens(
    db_session: Session, entity: UUID, admin: Actor
) -> None:
    service = IvaPeriodLockService(db_session)
    data = IvaPeriodLockCreate(year=2026, number=1, legal_entity_id=entity)
    collaborator = Actor(id=uuid4(), type="user", role="collaborator")
    with pytest.raises(PermissionDenied):
        service.close_iva_period(data, collaborator)
    with pytest.raises(AgentForbidden):
        service.close_iva_period(data, Actor(id=uuid4(), type="mcp", role="admin"))
    # A token with full access under an administrator is let in.
    owner = Actor(id=admin.id, type="mcp", role="admin", full_access=True)
    service.close_iva_period(data, owner)
    with pytest.raises(AgentForbidden):
        service.reopen_iva_period(2026, 1, Actor(id=uuid4(), type="mcp", role="admin"))
    service.reopen_iva_period(2026, 1, owner, legal_entity_id=entity)


def test_an_entity_with_no_periodic_iva_has_nothing_to_close(
    db_session: Session, entity: UUID, admin: Actor
) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), SYSTEM)
    with pytest.raises(Exception) as refused:  # noqa: PT011
        _close(db_session, admin, entity)
    assert type(refused.value).__name__ == "ValidationFailed"


# --- purchases ------------------------------------------------------------------------------


def test_a_purchase_invoice_is_refused_every_way_in_a_closed_period_and_allowed_once_reopened(
    db_session: Session, entity: UUID, admin: Actor, category: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    inside = _purchase(service, category, supplier, D(2026, 2, 10))
    outside = _purchase(service, category, supplier, D(2026, 5, 10), number="FA/13")
    cancelled = _purchase(service, category, supplier, D(2026, 2, 11), number="FA/14")
    service.cancel(cancelled.id, SYSTEM)
    _close(db_session, admin, entity)
    sentence = "il periodo IVA 1° trimestre 2026 è chiuso"

    with pytest.raises(Conflict) as created:
        _purchase(service, category, supplier, D(2026, 3, 31), number="FA/15")
    assert sentence in created.value.details["reason"]
    assert created.value.details["field"] == "iva_period"
    with pytest.raises(Conflict) as edited:
        service.update(inside.id, PurchaseUpdate(description="Altro"), SYSTEM)
    assert sentence in edited.value.details["reason"]
    # Out of the period is a write into it, and so is into it from outside.
    with pytest.raises(Conflict):
        service.update(inside.id, PurchaseUpdate(iva_period=D(2026, 4, 2)), SYSTEM)
    with pytest.raises(Conflict):
        service.update(outside.id, PurchaseUpdate(iva_period=D(2026, 2, 2)), SYSTEM)
    with pytest.raises(Conflict):
        service.cancel(inside.id, SYSTEM)
    with pytest.raises(Conflict):
        service.soft_delete(inside.id, SYSTEM)
    # A cancelled purchase is in no register: deleting it is no register write, and restoring
    # one that is not deleted is nothing.
    service.soft_delete(cancelled.id, SYSTEM)
    service.restore(cancelled.id, SYSTEM)
    # The open period and the next quarter are untouched.
    service.update(outside.id, PurchaseUpdate(description="Altro"), SYSTEM)
    # A purchase received after the quarter but placed in it by a person is refused too.
    with pytest.raises(Conflict):
        _purchase(
            service, category, supplier, D(2026, 4, 3), number="FA/16", iva_period=D(2026, 3, 30)
        )

    IvaPeriodLockService(db_session).reopen_iva_period(2026, 1, admin, legal_entity_id=entity)
    service.update(inside.id, PurchaseUpdate(description="Altro"), SYSTEM)
    service.cancel(inside.id, SYSTEM)
    _purchase(service, category, supplier, D(2026, 3, 31), number="FA/17")


def test_a_deleted_register_purchase_cannot_come_back_into_a_closed_period(
    db_session: Session, entity: UUID, admin: Actor, category: UUID
) -> None:
    service = PurchaseService(db_session)
    purchase = _purchase(service, category, _supplier(db_session), D(2026, 2, 10))
    service.soft_delete(purchase.id, SYSTEM)
    _close(db_session, admin, entity)
    with pytest.raises(Conflict) as restored:
        service.restore(purchase.id, SYSTEM)
    assert "1° trimestre 2026" in restored.value.details["reason"]


def test_an_expense_dated_in_a_closed_period_is_in_no_register(
    db_session: Session, entity: UUID, admin: Actor, category: UUID
) -> None:
    _close(db_session, admin, entity)
    service = PurchaseService(db_session)
    expense = service.create(
        PurchaseCreate(
            category_id=category,
            date=D(2026, 2, 10),
            amount=Decimal("10.00"),
            description="Taxi",
        ),
        SYSTEM,
    )
    service.update(expense.id, PurchaseUpdate(description="Taxi"), SYSTEM)
    service.cancel(expense.id, SYSTEM)


def test_another_legal_entity_is_not_held_by_the_lock(
    db_session: Session, entity: UUID, admin: Actor, category: UUID
) -> None:
    from pigrocrm.core.legal_entities.models import LegalEntity

    other = LegalEntity(name="Altra", legal_name="Altra Srl", country="IT")
    db_session.add(other)
    db_session.flush()
    _close(db_session, admin, entity)
    service = IvaPeriodLockService(db_session)
    service.assert_writable("purchase", "iva_period", other.id, D(2026, 2, 10))
    service.assert_writable("purchase", "iva_period", None, D(2026, 2, 10))
    with pytest.raises(Conflict):
        service.assert_writable("purchase", "iva_period", entity, D(2026, 2, 10))


# --- sales ----------------------------------------------------------------------------------


def _issue(service: InvoiceService, customer: UUID, day: date | None):  # noqa: ANN202
    draft = service.create(
        InvoiceCreate(
            customer_id=customer,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        SYSTEM,
    )
    return service.issue(draft.id, InvoiceIssue(issue_date=day), SYSTEM)


def test_an_invoice_cannot_be_issued_or_annulled_with_a_date_in_a_closed_period(
    db_session: Session, invoices: InvoiceService, entity: UUID, admin: Actor
) -> None:
    customer = _customer(db_session)
    issued = _issue(invoices, customer, D(2026, 2, 10))
    later = _issue(invoices, customer, D(2026, 5, 10))
    draft = invoices.create(
        InvoiceCreate(
            customer_id=customer,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        SYSTEM,
    )
    _close(db_session, admin, entity)

    with pytest.raises(Conflict) as issuing:
        invoices.issue(draft.id, InvoiceIssue(issue_date=D(2026, 3, 2)), SYSTEM)
    assert "il periodo IVA 1° trimestre 2026 è chiuso" in issuing.value.details["reason"]
    assert issuing.value.details["field"] == "issue_date"
    with pytest.raises(Conflict) as annulling:
        invoices.annul(issued.id, InvoiceAnnul(reason="errore"), SYSTEM)
    assert "1° trimestre 2026" in annulling.value.details["reason"]
    # Nothing was consumed, and the open quarter is untouched; a payment is not a register write.
    invoices.annul(later.id, InvoiceAnnul(reason="errore"), SYSTEM)

    IvaPeriodLockService(db_session).reopen_iva_period(2026, 1, admin, legal_entity_id=entity)
    invoices.annul(issued.id, InvoiceAnnul(reason="errore"), SYSTEM)


def test_an_invoice_imported_with_a_date_in_a_closed_period_is_refused(
    db_session: Session, invoices: InvoiceService, entity: UUID, admin: Actor
) -> None:
    customer = _customer(db_session)
    _close(db_session, admin, entity)
    data = InvoiceImport(
        year=2026,
        number=1,
        issue_date=D(2026, 2, 10),
        due_date=D(2026, 2, 28),
        customer_id=customer,
        memo="Consulenza",
        lines=[
            {
                "description": "Consulenza",
                "quantity": Decimal("1"),
                "unit_price": Decimal("1000"),
                "total_price": Decimal("1000.00"),
                "vat_rate": Decimal("22"),
            }
        ],
        taxable_amount=Decimal("1000.00"),
        vat_amount=Decimal("220.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("1220.00"),
        payment_status="unpaid",
    )
    with pytest.raises(Conflict) as refused:
        invoices.import_issued(data, SYSTEM)
    assert "1° trimestre 2026" in refused.value.details["reason"]


def test_the_detail_reads_say_when_a_purchase_or_an_invoice_is_in_a_closed_period(
    db_session: Session, invoices: InvoiceService, entity: UUID, admin: Actor, category: UUID
) -> None:
    service = PurchaseService(db_session)
    purchase = _purchase(service, category, _supplier(db_session), D(2026, 2, 10))
    outside = _purchase(service, category, _supplier(db_session), D(2026, 5, 10), number="FA/13")
    issued = _issue(invoices, _customer(db_session), D(2026, 2, 10))
    assert not service.get(purchase.id, SYSTEM).iva_period_closed
    assert not invoices.get(issued.id, SYSTEM).iva_period_closed

    _close(db_session, admin, entity)

    assert service.get(purchase.id, SYSTEM).iva_period_closed
    assert not service.get(outside.id, SYSTEM).iva_period_closed
    assert invoices.get(issued.id, SYSTEM).iva_period_closed
