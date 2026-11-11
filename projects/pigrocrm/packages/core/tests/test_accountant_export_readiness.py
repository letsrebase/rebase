"""Is the period ready, and what the payroll consultant is told (REB-1048, accountant export
design § 2 and § 4).

What these hold the code to: the checklist names each thing that waits (an unmatched movement, a
payslip nobody accepted, a purchase with no document, an invoice with no file, an inbox item, a
statement that is missing, an IVA period that is not closed), draws only the checks that apply to
the company's modules and pack and to what is asked, and is an administrator's; a build computes it
again and stores that snapshot, which a later fix never rewrites; «variazioni paghe» lists a hire,
a person who left, a person whose record was edited and an approved expense note to reimburse in
the payslip, for an administrator who may read pay and for no one else.
"""

import datetime as dt
import io
import zipfile
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tax_period import TODAY
from test_accountant_export import _customer, _issue
from test_expense_notes import World

from conftest import _invoice_service
from pigrocrm.core.accountant_export.models import AccountantExportRow
from pigrocrm.core.accountant_export.parts import Period
from pigrocrm.core.accountant_export.schemas import ChecklistCheckRead, ExportKind
from pigrocrm.core.accountant_export.service import AccountantExportService, StoredExport
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.models import BankAccount, BankMovement, BankStatement
from pigrocrm.core.config import get_settings
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.errors import (
    AgentForbidden,
    ModuleOff,
    NotFound,
    PermissionDenied,
    ScopedAdmin,
    ValidationFailed,
)
from pigrocrm.core.expense_notes.schemas import ExpenseNoteReimburse
from pigrocrm.core.expense_notes.service import ExpenseNoteService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.purchases.schemas import PurchaseCreate
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.staff.estimate import add_months, first_of
from pigrocrm.core.staff.schemas import PayslipFigures, StaffMemberUpdate
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.storage.local import LocalFileStorage

D = dt.date
ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")
FULL_ACCESS_AGENT = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
LAST_MONTH = add_months(first_of(TODAY), -1)
PERIOD = Period(*month_bounds(LAST_MONTH.year, LAST_MONTH.month))
MIDDLE = PERIOD.date_from + dt.timedelta(days=9)
NOW = dt.datetime.combine(TODAY, dt.time(9, 30), dt.UTC)
OFF = {"module_cash": False, "module_purchases": False, "module_staff": False}


@pytest.fixture
def world(db_session: Session) -> World:
    return World(db_session)


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> AccountantExportService:
    return AccountantExportService(db_session, local_storage, get_settings())


def _checks(
    service: AccountantExportService, world: World, **kwargs: Any
) -> dict[str, ChecklistCheckRead]:
    read = service.get_export_checklist(PERIOD, ADMIN, world.entity, **kwargs)
    return {check.key: check for check in read.checks}


def _figures() -> PayslipFigures:
    return PayslipFigures(
        period=LAST_MONTH,
        gross=Decimal("2142.86"),
        net=Decimal("1607.15"),
        withholding=Decimal("321.43"),
    )


def _account(world: World, *, statement: bool = True) -> BankAccount:
    account = BankAccount(
        legal_entity_id=world.entity,
        name="Conto principale",
        bank="Banco BPM",
        iban="IT60X0542811101000000123456",
        created_at=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
    )
    world.session.add(account)
    world.session.flush()
    if statement:
        world.session.add(
            BankStatement(
                bank_account_id=account.id,
                legal_entity_id=world.entity,
                period_from=PERIOD.date_from,
                period_to=PERIOD.date_to,
                format="test",
                state="imported",
            )
        )
    world.session.flush()
    return account


def _movement(world: World, account: BankAccount, state: str = "unmatched") -> BankMovement:
    movement = BankMovement(
        bank_account_id=account.id,
        legal_entity_id=world.entity,
        booking_date=MIDDLE,
        amount=Decimal("-120.00"),
        currency="EUR",
        counterparty_name="Fornitore Rossi",
        dedup_key=uuid4().hex,
        state=state,
    )
    world.session.add(movement)
    world.session.flush()
    return movement


def _inbox_item(world: World, **overrides: Any) -> InboxItem:
    values: dict[str, Any] = {
        "original_name": "documento.pdf",
        "size_bytes": 10,
        "sha256": uuid4().hex * 2,
        "content_sha256": uuid4().hex * 2,
        "state": "new",
        "legal_entity_id": world.entity,
        "created_at": dt.datetime.combine(MIDDLE, dt.time(12), dt.UTC),
    }
    values.update(overrides)
    item = InboxItem(**values)
    world.session.add(item)
    world.session.flush()
    return item


def _waiting_payslip(world: World) -> None:
    """A payslip the inbox read for the member's month and nobody accepted."""
    item = _inbox_item(world, original_name="cedolino.pdf", state="proposed", kind="payslip")
    world.session.add(
        Proposal(
            inbox_item_id=item.id,
            target_type="payslip",
            proposed_fields={
                "staff_member_id": str(world.member.id),
                "period": LAST_MONTH.isoformat(),
                "gross": "2142.86",
                "net": "1607.15",
                "withholding": "321.43",
            },
            excerpt="Netto in busta",
            confidence=Decimal("0.90"),
            status="pending",
        )
    )
    world.session.flush()


# --- the checklist -------------------------------------------------------------------------


def test_a_period_with_an_unmatched_movement_and_an_unaccepted_payslip_shows_both(
    world: World, service: AccountantExportService
) -> None:
    _movement(world, _account(world))
    _waiting_payslip(world)

    checks = _checks(service, world, payroll_parts=["payslips"])

    assert checks["bank_movements"].state == "open" and checks["bank_movements"].count == 1
    assert checks["bank_movements"].items[0].endswith("Fornitore Rossi")
    assert checks["bank_movements"].target == "cash"
    payroll = checks["payroll"]
    assert payroll.state == "open" and payroll.target == "staff"
    # The member whose payslip was read and not accepted, and the one with nothing at all.
    assert any("Carla Rossi" in item and item.endswith("da accettare") for item in payroll.items)
    assert any("Dario Neri" in item and item.endswith("mancante") for item in payroll.items)
    # A statement covers the month and nothing else waits.
    assert checks["bank_statements"].state == "ok"
    assert checks["inbox"].state == "ok"


def test_a_matched_movement_and_an_accepted_payslip_leave_the_checks_fine(
    world: World, service: AccountantExportService
) -> None:
    _movement(world, _account(world), state="matched")
    staff = StaffService(world.session)
    staff.record_staff_payslip(world.member.id, _figures(), world.admin)
    staff.record_staff_payslip(world.no_user.id, _figures(), world.admin)

    checks = _checks(service, world, payroll_parts=["payslips"])

    assert checks["bank_movements"].state == "ok" and checks["bank_movements"].count == 0
    assert checks["payroll"].state == "ok"


def test_the_payroll_check_is_drawn_only_when_the_payslips_are_asked_for(
    world: World, service: AccountantExportService
) -> None:
    assert "payroll" not in _checks(service, world)
    assert "payroll" in _checks(service, world, payroll_parts=["payslips"])
    with pytest.raises(ValidationFailed, match="parte paghe sconosciuta"):
        _checks(service, world, payroll_parts=["f24"])


def test_a_check_of_a_module_that_is_off_is_not_drawn(
    world: World, db_session: Session, local_storage: LocalFileStorage
) -> None:
    off = AccountantExportService(db_session, local_storage, get_settings().model_copy(update=OFF))

    keys = [check.key for check in off.get_export_checklist(PERIOD, ADMIN, world.entity).checks]

    assert keys == ["inbox", "invoice_files"]


def test_a_purchase_without_its_document_and_an_inbox_item_waiting_are_named(
    world: World, service: AccountantExportService
) -> None:
    PurchaseService(world.session).create(
        PurchaseCreate(
            kind="receipt",
            date=MIDDLE,
            amount=Decimal("18.00"),
            category_id=world.category,
            legal_entity_id=world.entity,
            description="Cancelleria",
        ),
        world.admin,
    )
    _inbox_item(world, original_name="fattura-luce.pdf")

    checks = _checks(service, world)

    assert checks["purchase_documents"].count == 1
    assert "Cancelleria" in checks["purchase_documents"].items[0]
    assert "nessun documento" in checks["purchase_documents"].items[0]
    assert checks["inbox"].items == ["fattura-luce.pdf"]
    assert checks["purchase_documents"].target == "purchases"


def test_an_invoice_with_no_stored_file_is_named_by_its_number(
    db_session: Session, local_storage: LocalFileStorage, service: AccountantExportService
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    invoices = _invoice_service(db_session, local_storage)
    day = D(TODAY.year, 1, 1)
    _issue(invoices, _customer(db_session), "100.00", day, files=False)

    read = service.get_export_checklist(Period(day, day), ADMIN, entity.id)
    check = next(c for c in read.checks if c.key == "invoice_files")

    assert check.state == "open" and check.count == 1
    assert check.items == [f"{TODAY.year}/1"]


def test_a_month_no_statement_covers_is_a_gap_and_a_long_list_is_counted_not_listed(
    world: World, service: AccountantExportService
) -> None:
    account = _account(world, statement=False)
    for _ in range(7):
        _movement(world, account)

    checks = _checks(service, world)

    assert checks["bank_statements"].state == "open"
    assert "estratto mancante" in checks["bank_statements"].items[0]
    assert checks["bank_movements"].count == 7
    assert len(checks["bank_movements"].items) == 5 and checks["bank_movements"].more == 2


def test_an_iva_period_that_ended_and_is_not_closed_is_open_only_for_a_pack_with_iva(
    db_session: Session, world: World, service: AccountantExportService
) -> None:
    # `it-standard` settles IVA by period; the period that ended is not closed.
    checks = _checks(service, world)
    assert checks["iva_period"].state == "open" and checks["iva_period"].target == "iva"
    flat = LegalEntity(name="Forfettaria", legal_name="Forfettaria", country="IT")
    db_session.add(flat)
    db_session.flush()
    db_session.add(
        FiscalProfile(legal_entity_id=flat.id, pack_id="it-flat-rate", regime_code="RF19")
    )
    db_session.flush()

    read = service.get_export_checklist(PERIOD, ADMIN, flat.id)

    assert "iva_period" not in {check.key for check in read.checks}


# --- who asks and what is stored -------------------------------------------------------------


def test_only_an_administrator_reads_the_checklist(
    world: World, service: AccountantExportService
) -> None:
    with pytest.raises(PermissionDenied):
        service.get_export_checklist(PERIOD, COLLABORATOR, world.entity)
    with pytest.raises(AgentForbidden):
        service.get_export_checklist(PERIOD, AGENT, world.entity)
    assert service.get_export_checklist(PERIOD, FULL_ACCESS_AGENT, world.entity).checks
    scoped = Actor(id=uuid4(), type="user", role="admin", legal_entities=(uuid4(),))
    with pytest.raises(NotFound):
        service.get_export_checklist(PERIOD, scoped, world.entity)
    with pytest.raises(ScopedAdmin):
        service.get_export_checklist(PERIOD, scoped, all_entities=True)


def _readiness(checklist: dict[str, object]) -> dict[str, Any]:
    readiness = checklist["readiness"]
    assert isinstance(readiness, dict)
    return readiness


def test_an_export_built_anyway_stores_the_snapshot_and_a_later_fix_never_rewrites_it(
    world: World, service: AccountantExportService
) -> None:
    movement = _movement(world, _account(world))

    stored = service.build_accountant_export(PERIOD, ADMIN, world.entity, now=NOW)

    snapshot = _readiness(stored.export.checklist)
    assert snapshot["open_count"] >= 1
    movements = next(c for c in snapshot["checks"] if c["key"] == "bank_movements")
    assert movements["count"] == 1 and movements["state"] == "open"
    # The same snapshot is in the ZIP's index, where the accountant reads it.
    data, _, _ = service.download_part(stored.export.id, 1, ADMIN)
    index = zipfile.ZipFile(io.BytesIO(data)).read("indice.csv").decode()
    assert "Il periodo è pronto" in index
    assert "Movimenti di banca da abbinare;Da vedere;1" in index
    # Fixing the movement afterwards leaves what the period looked like when it was built.
    movement.state = "matched"
    world.session.flush()
    row = world.session.get(AccountantExportRow, stored.export.id)
    assert row is not None
    assert _readiness(row.checklist) == snapshot
    assert _checks(service, world)["bank_movements"].state == "ok"


# --- «variazioni paghe» -----------------------------------------------------------------------


def _payroll_changes(
    service: AccountantExportService, world: World, actor: Actor = ADMIN
) -> StoredExport:
    kind: ExportKind = "payroll_changes"
    return service.build_accountant_export(PERIOD, actor, world.entity, kind=kind, now=NOW)


def _file(service: AccountantExportService, export_id: UUID, name: str) -> list[list[str]]:
    data, _, _ = service.download_part(export_id, 1, ADMIN)
    text = zipfile.ZipFile(io.BytesIO(data)).read(name).decode().removeprefix("\ufeff")
    return [line.split(";") for line in text.split("\r\n") if line][2:]


def _edited_in_the_period(world: World) -> None:
    """The timeline stamps the real clock; the period is the frozen one."""
    for activity in world.session.scalars(
        select(Activity).where(Activity.kind == "staff_member_updated")
    ):
        activity.occurred_at = dt.datetime.combine(MIDDLE, dt.time(11), dt.UTC)
    world.session.flush()


def test_variazioni_paghe_lists_a_new_hire_a_leaver_an_edited_record_and_an_approved_note(
    world: World, service: AccountantExportService
) -> None:
    staff = StaffService(world.session)
    staff.create_staff_member(world._employee(name="Elena Verdi", start_date=MIDDLE), world.admin)
    staff.update_staff_member(
        world.no_user.id, StaffMemberUpdate(end_date=MIDDLE + dt.timedelta(days=5)), world.admin
    )
    staff.update_staff_member(
        world.member.id, StaffMemberUpdate(gross_annual_pay=Decimal("33000.00")), world.admin
    )
    _edited_in_the_period(world)
    notes = ExpenseNoteService(world.session)
    note_id = world.submit(date=MIDDLE, description="Treno per il cliente")
    notes.approve_expense_note(note_id, world.other_admin)
    notes.reimburse_expense_note(
        note_id,
        ExpenseNoteReimburse(method="payslip", payroll_month=LAST_MONTH),
        world.other_admin,
    )
    by_transfer = world.submit(date=MIDDLE, description="Taxi")
    notes.approve_expense_note(by_transfer, world.other_admin)
    notes.reimburse_expense_note(
        by_transfer, ExpenseNoteReimburse(method="transfer", date=TODAY), world.other_admin
    )

    stored = _payroll_changes(service, world)

    export_id = stored.export.id
    assert stored.export.kind == "payroll_changes" and stored.export.payroll is True
    hires = _file(service, export_id, "assunzioni/elenco.csv")
    assert [row[0] for row in hires] == ["Elena Verdi"] and hires[0][1] == MIDDLE.isoformat()
    leavers = _file(service, export_id, "cessazioni/elenco.csv")
    assert [row[0] for row in leavers] == ["Dario Neri"]
    changes = _file(service, export_id, "variazioni/elenco.csv")
    assert [row[0] for row in changes] == ["Carla Rossi"]
    assert "33.000,00" in changes[0] and changes[0][-1].startswith("condizioni attuali")
    expense = _file(service, export_id, "note-spese/elenco.csv")
    assert [(row[0], row[2], row[6]) for row in expense] == [
        ("Carla Rossi", "Treno per il cliente", "Da rimborsare")
    ]
    assert expense[0][4] == "42,50" and expense[0][5] == f"{LAST_MONTH:%Y-%m}"
    assert stored.index.readiness is None and "readiness" not in stored.export.checklist
    row = world.session.get(AccountantExportRow, export_id)
    assert row is not None and row.kind == "payroll_changes" and row.payroll is True


def test_a_record_edited_in_the_month_it_was_made_is_a_hire_and_not_a_change(
    world: World, service: AccountantExportService
) -> None:
    staff = StaffService(world.session)
    hire = staff.create_staff_member(
        world._employee(name="Elena Verdi", start_date=MIDDLE), world.admin
    )
    staff.update_staff_member(hire.id, StaffMemberUpdate(contract_level="4"), world.admin)
    _edited_in_the_period(world)

    stored = _payroll_changes(service, world)

    assert _file(service, stored.export.id, "variazioni/elenco.csv") == []
    assert len(_file(service, stored.export.id, "assunzioni/elenco.csv")) == 1


def test_variazioni_paghe_is_for_an_administrator_who_reads_pay_and_one_company(
    world: World,
    service: AccountantExportService,
    db_session: Session,
    local_storage: LocalFileStorage,
) -> None:
    with pytest.raises(PermissionDenied):
        _payroll_changes(service, world, COLLABORATOR)
    with pytest.raises(AgentForbidden):
        _payroll_changes(service, world, AGENT)
    assert _payroll_changes(service, world, FULL_ACCESS_AGENT).export.kind == "payroll_changes"
    with pytest.raises(ValidationFailed):
        service.build_accountant_export(
            PERIOD, ADMIN, all_entities=True, kind="payroll_changes", now=NOW
        )
    scoped = Actor(id=uuid4(), type="user", role="admin", legal_entities=(uuid4(),))
    with pytest.raises(NotFound):
        _payroll_changes(service, world, scoped)
    off = AccountantExportService(
        db_session, local_storage, get_settings().model_copy(update={"module_staff": False})
    )
    with pytest.raises(ModuleOff):
        _payroll_changes(off, world)


def test_the_payslips_are_a_part_only_when_asked_and_make_the_export_carry_pay(
    world: World, service: AccountantExportService
) -> None:
    plain = service.build_accountant_export(PERIOD, ADMIN, world.entity, now=NOW)
    assert plain.export.payroll is False and plain.export.kind == "accountant"
    assert "payslips" not in {line.key for line in plain.index.parts}
    StaffService(world.session).record_staff_payslip(world.member.id, _figures(), world.admin)

    asked = service.build_accountant_export(
        PERIOD, ADMIN, world.entity, payroll_parts=["payslips"], now=NOW
    )

    assert asked.export.payroll is True
    rows = _file(service, asked.export.id, "paghe/elenco.csv")
    assert [(row[1], row[2]) for row in rows] == [("Carla Rossi", "2.142,86")]
    # A payslip typed by a person has no file: said so in the index, not left out.
    assert any(item.missing.document.endswith("Carla Rossi") for item in asked.index.missing)
    parts = service.describe_parts(ADMIN, world.entity)
    assert {part.opt_in for part in parts} == {None, "payslips"}
    changes = service.describe_parts(ADMIN, world.entity, kind="payroll_changes")
    assert [part.key for part in changes] == [
        "staff_hires",
        "staff_leavers",
        "staff_pay_changes",
        "staff_expense_notes",
    ]
