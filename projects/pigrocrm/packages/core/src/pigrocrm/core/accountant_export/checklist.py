"""Is the period ready? The checklist an export carries (spec accountant export § 2).

Seven checks, each a count of open points with the first few named: the inbox items of the period
still waiting, the purchases without their document and the proposals not accepted, the issued
invoices with no stored file, the bank statements missing or waiting, the bank movements not
matched, an IVA period not closed and, when the payslips are asked for, the payslips not received
or not accepted. A check of a module that is off, of a pack the check does not apply to or of a
part that was not asked for is not in the list: the page never names it.

Counts, never figures: the checklist says how many things wait, and the books are the registers'.
An export with open points is allowed; it says so. The result is computed once per call, from the
database as it is now, and the build stores it with the export so a later fix never rewrites what
the period looked like when the file was made.

The rows each check reads sit where their own module keeps them; the checks only ask. A record is
placed in the period by the date its module places it by: an inbox item by the day it arrived, a
purchase and a movement by their own date, an invoice by its issue date, a missing statement by
the month it should cover, a payslip by the month it pays.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, Date, cast, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.parts import Period
from pigrocrm.core.accountant_export.repository import ExportRepository
from pigrocrm.core.accountant_export.schemas import ChecklistCheckRead, ChecklistRead
from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.coverage import statement_gaps
from pigrocrm.core.bank.models import BankAccount, BankMovement
from pigrocrm.core.config import Settings
from pigrocrm.core.db.clock import ITALY_TZ, month_bounds, today_local
from pigrocrm.core.fiscal.iva_register import IvaRegisterService
from pigrocrm.core.fiscal.pack import resolve_pack
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.inbox.models import PAY_KINDS, InboxItem
from pigrocrm.core.invoices.artifacts import InvoiceArtifacts
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.modules.registry import ModuleKey, missing_modules
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.staff.payroll import PayrollService

# How many open points a check names; the rest are counted (`more`), never listed. The index in
# the ZIP names every invoice with no file, the page only the first few.
ITEMS_SHOWN = 5
# The proposals a purchase is made from.
PURCHASE_PROPOSALS = ("supplier_invoice", "receipt")
MONTHS_IT = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)


@dataclass(frozen=True)
class Unit:
    """What one pass of the checks reads: one legal entity, or the records that belong to none.
    `with_shared` adds the records of no legal entity to an entity's own (a space with one
    entity, where «shared» can only be its), and `name` prefixes each point when more than one
    unit is read, so the page tells which company a point is in."""

    legal_entity: LegalEntity | None
    with_shared: bool
    name: str | None

    @property
    def legal_entity_id(self) -> UUID | None:
        return self.legal_entity.id if self.legal_entity is not None else None

    def scope(self, column: ColumnElement[UUID | None]) -> ColumnElement[bool]:
        """The rows of this unit: the entity's own, plus the shared ones when they are its."""
        if self.legal_entity is None:
            return column.is_(None)
        if self.with_shared:
            return or_(column == self.legal_entity.id, column.is_(None))
        return column == self.legal_entity.id

    def say(self, point: str) -> str:
        return f"{self.name}: {point}" if self.name else point


@dataclass(frozen=True)
class Reading:
    """What every check reads from: the database as the actor sees it, the period and today."""

    session: Session
    actor: Actor
    period: Period
    today: dt.date


Collector = Callable[[Reading, Unit], list[str]]


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    # Where on the page the point is fixed: a route key the web maps to its own route.
    target: str
    collect: Collector
    # The module that has to be on, or the check is not drawn.
    module: ModuleKey | None = None
    # Only for the units that are a legal entity, not the shared section.
    entity_only: bool = False
    # Drawn only when the export carries the payslips.
    payroll: bool = False
    # Whether the check applies to this entity at all (an IVA check for a pack with no IVA by
    # period does not): a check that applies to no unit is not drawn, as one whose module is off.
    applies: Callable[[Reading, Unit], bool] = lambda read, unit: True


def _arrived(column: ColumnElement[dt.datetime], period: Period) -> ColumnElement[bool]:
    """The day an instant fell in Rome, inside the period."""
    day = cast(func.timezone(ITALY_TZ.key, column), Date)
    return (day >= period.date_from) & (day <= period.date_to)


def _waiting_proposals(read: Reading, unit: Unit, targets: tuple[str, ...], note: str) -> list[str]:
    """The inbox items of the period a person has not accepted a proposal of."""
    rows = read.session.execute(
        select(InboxItem.original_name)
        .join(Proposal, Proposal.inbox_item_id == InboxItem.id)
        .where(
            Proposal.status == "pending",
            Proposal.target_type.in_(targets),
            unit.scope(InboxItem.legal_entity_id),
            _arrived(InboxItem.created_at, read.period),
        )
        .order_by(InboxItem.created_at, InboxItem.id)
    )
    return [unit.say(f"{name}: {note}") for (name,) in rows]


def _inbox(read: Reading, unit: Unit) -> list[str]:
    """Items nobody looked at yet. A payslip or the consultant's F24 is pay, read under the
    payroll check, never here."""
    rows = read.session.scalars(
        select(InboxItem.original_name)
        .where(
            InboxItem.state == "new",
            or_(InboxItem.kind.is_(None), InboxItem.kind.not_in(PAY_KINDS)),
            unit.scope(InboxItem.legal_entity_id),
            _arrived(InboxItem.created_at, read.period),
        )
        .order_by(InboxItem.created_at, InboxItem.id)
    )
    return [unit.say(name) for name in rows]


def _purchase_documents(read: Reading, unit: Unit) -> list[str]:
    purchases = read.session.scalars(
        select(Purchase)
        .where(
            unit.scope(Purchase.legal_entity_id),
            Purchase.status == "registered",
            Purchase.deleted_at.is_(None),
            Purchase.document_id.is_(None),
            Purchase.date >= read.period.date_from,
            Purchase.date <= read.period.date_to,
        )
        .order_by(Purchase.date, Purchase.created_at, Purchase.id)
    )
    points = [
        unit.say(
            f"{purchase.number or purchase.description or purchase.merchant_name or 'acquisto'} "
            f"del {purchase.date.strftime('%d/%m/%Y')}: nessun documento"
        )
        for purchase in purchases
    ]
    return points + _waiting_proposals(read, unit, PURCHASE_PROPOSALS, "proposta da accettare")


def _invoice_files(read: Reading, unit: Unit) -> list[str]:
    assert unit.legal_entity is not None
    points: list[str] = []
    for invoice in ExportRepository(read.session).numbered_invoices(
        unit.legal_entity.id, read.period.date_from, read.period.date_to
    ):
        abroad = InvoiceArtifacts.frozen_abroad(invoice)
        if invoice.pdf_document_id is None or (invoice.xml_document_id is None and not abroad):
            points.append(unit.say(f"{invoice.year}/{invoice.number}"))
    return points


def _statements(read: Reading, unit: Unit) -> list[str]:
    assert unit.legal_entity is not None
    points: list[str] = []
    accounts = read.session.scalars(
        select(BankAccount)
        .where(
            BankAccount.legal_entity_id == unit.legal_entity.id,
            BankAccount.archived_at.is_(None),
        )
        .order_by(BankAccount.name, BankAccount.id)
    )
    for account in accounts:
        for gap in statement_gaps(
            read.session, account, read.period.date_from, read.period.date_to, read.today
        ):
            points.append(
                unit.say(
                    f"{account.name}: estratto mancante dal {gap.period_from.strftime('%d/%m/%Y')} "
                    f"al {gap.period_to.strftime('%d/%m/%Y')}"
                )
            )
    return points + _waiting_proposals(read, unit, ("bank_statement",), "estratto da accettare")


def _movements(read: Reading, unit: Unit) -> list[str]:
    assert unit.legal_entity is not None
    rows = read.session.execute(
        select(BankMovement.booking_date, BankMovement.counterparty_name, BankMovement.description)
        .where(
            BankMovement.legal_entity_id == unit.legal_entity.id,
            BankMovement.state == "unmatched",
            BankMovement.booking_date >= read.period.date_from,
            BankMovement.booking_date <= read.period.date_to,
        )
        .order_by(BankMovement.booking_date, BankMovement.id)
    )
    return [
        unit.say(f"{booked.strftime('%d/%m/%Y')} {(who or description or 'movimento')[:40]}")
        for booked, who, description in rows
    ]


def _has_periodic_iva(read: Reading, unit: Unit) -> bool:
    assert unit.legal_entity is not None
    profile = FiscalProfileRepository(read.session).get(unit.legal_entity.id)
    return (
        profile is not None and resolve_pack(profile.pack_id, profile.pack_version).has_periodic_iva
    )


def _iva(read: Reading, unit: Unit) -> list[str]:
    """The IVA periods the export's days touch that nobody closed: one still running, or one
    that has ended and is not closed. A pack with no IVA by period answers no periods."""
    assert unit.legal_entity is not None
    service = IvaRegisterService(read.session)
    period = read.period
    points: list[str] = []
    for year in range(period.date_from.year, period.date_to.year + 1):
        for iva in service.list_periods(year, read.actor, legal_entity_id=unit.legal_entity.id):
            touches = iva.period_start <= period.date_to and iva.period_end >= period.date_from
            if touches and iva.state != "future" and not iva.closed:
                points.append(unit.say(iva.label))
    return points


def _payroll(read: Reading, unit: Unit) -> list[str]:
    """Employees whose payslip of a month that ended is not accepted: none was received, or one
    was read from the inbox and nobody accepted it."""
    assert unit.legal_entity is not None
    service = PayrollService(read.session)
    points: list[str] = []
    month = read.period.date_from.replace(day=1)
    while month <= read.period.date_to:
        if month_bounds(month.year, month.month)[1] < read.today:
            payroll_month = service.get_payroll_month(unit.legal_entity.id, month, read.actor)
            for row in payroll_month.members:
                if row.kind != "employee" or row.payslip is not None:
                    continue
                what = f"{row.name}: cedolino di {MONTHS_IT[month.month - 1]} {month.year}"
                points.append(
                    unit.say(what + (" da accettare" if row.received is not None else " mancante"))
                )
        month = (month + dt.timedelta(days=32)).replace(day=1)
    return points


# The order is the page's.
CHECKS: tuple[Check, ...] = (
    Check("inbox", "Documenti in arrivo ancora da guardare", "inbox", _inbox),
    Check(
        "purchase_documents",
        "Acquisti senza documento o proposte da accettare",
        "purchases",
        _purchase_documents,
        module="purchases",
    ),
    Check(
        "invoice_files",
        "Fatture emesse senza file archiviato",
        "invoices",
        _invoice_files,
        entity_only=True,
    ),
    Check(
        "bank_statements",
        "Estratti conto mancanti o da accettare",
        "cash",
        _statements,
        module="cash",
        entity_only=True,
    ),
    Check(
        "bank_movements",
        "Movimenti di banca da abbinare",
        "cash",
        _movements,
        module="cash",
        entity_only=True,
    ),
    Check(
        "iva_period",
        "Periodo IVA da chiudere",
        "iva",
        _iva,
        module="purchases",
        entity_only=True,
        applies=_has_periodic_iva,
    ),
    Check(
        "payroll",
        "Cedolini del periodo",
        "staff",
        _payroll,
        module="staff",
        entity_only=True,
        payroll=True,
    ),
)


def compute_checklist(
    session: Session,
    settings: Settings,
    actor: Actor,
    period: Period,
    units: list[Unit],
    *,
    payroll: bool,
) -> ChecklistRead:
    """The checklist of `period` for `units`. `payroll` is whether the payslips are part of the
    export, which is what puts the payroll check in the list."""
    read = Reading(session, actor, period, today_local(settings))
    checks: list[ChecklistCheckRead] = []
    for check in CHECKS:
        if (check.payroll and not payroll) or (
            check.module is not None and missing_modules(settings, (check.module,))
        ):
            continue
        points: list[str] = []
        applies = False
        for unit in units:
            if check.entity_only and unit.legal_entity is None:
                continue
            if not check.applies(read, unit):
                continue
            applies = True
            points.extend(check.collect(read, unit))
        if not applies:
            continue
        checks.append(
            ChecklistCheckRead(
                key=check.key,
                label=check.label,
                state="open" if points else "ok",
                count=len(points),
                items=points[:ITEMS_SHOWN],
                more=max(len(points) - ITEMS_SHOWN, 0),
                target=check.target,
            )
        )
    return ChecklistRead(
        period_from=period.date_from,
        period_to=period.date_to,
        open_count=sum(check.count for check in checks),
        checks=checks,
    )


__all__ = ["CHECKS", "ITEMS_SHOWN", "Unit", "compute_checklist"]
