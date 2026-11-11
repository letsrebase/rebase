"""The parts of the export that carry pay (spec accountant export § 1 and § 4): the payslips an
accountant may be given when the administrator ticks them, and the staff changes of the payroll
consultant's «variazioni paghe».

Both are an administrator's alone and neither travels on a plain link: the export row says it
carries pay (`payroll`) and the share link of such an export asks a one-time code. The names of
the people are in these files and in no other part of an export, which is why the payslips are
opt-in and the staff changes a separate export.

**Staff changes** are what the payroll consultant has to act on for the month: who was hired,
who left, whose terms changed, and the expense notes that are reimbursed in a payslip. A change
of terms is the one thing the data cannot say in figures: the record is edited in place and its
timeline says that it was, never from what to what (staff design § 4), so the file lists each
person whose record was edited in the period with the terms as they stand now, and says so.
"""

import datetime as dt
from decimal import Decimal
from posixpath import basename

from sqlalchemy import Date, Select, cast, func, select

from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    MissingFile,
    PartContext,
    PartOutput,
)
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.csv_cells import BOM, amount_cell, row_line, text_cell
from pigrocrm.core.db.clock import ITALY_TZ
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.staff.models import StaffMember, StaffPayslip

STAFF_ENTITY = "staff_member"
UPDATED_KIND = "staff_member_updated"
LIST_FILE = "elenco.csv"
KIND_WORDS = {"employee": "Dipendente", "director": "Amministratore"}

PAYSLIP_COLUMNS = ("Mese", "Persona", "Lordo", "Netto", "Ritenute", "File")
# What a person's terms are made of, after the name and the date a file puts first.
TERMS_COLUMNS = (
    "Tipo",
    "Contratto collettivo",
    "Livello",
    "Retribuzione annua lorda o compenso annuo",
    "Mensilità",
    "Percentuale di tempo parziale",
)
HIRE_COLUMNS = ("Persona", "Data di inizio", *TERMS_COLUMNS)
LEAVER_COLUMNS = ("Persona", "Tipo", "Data di fine")
CHANGE_COLUMNS = ("Persona", "Data dell'ultima modifica", *TERMS_COLUMNS, "Nota")
CHANGE_NOTE = "condizioni attuali: i valori precedenti non sono conservati"


def _members(ctx: PartContext) -> Select[tuple[StaffMember]]:
    assert ctx.legal_entity is not None
    return select(StaffMember).where(StaffMember.legal_entity_id == ctx.legal_entity.id)


def _terms(member: StaffMember) -> list[str]:
    amount: Decimal | None = member.gross_annual_pay or member.annual_fee
    return [
        text_cell(KIND_WORDS[member.kind]),
        text_cell(member.collective_agreement),
        text_cell(member.contract_level),
        amount_cell(amount) if amount is not None else "",
        str(member.monthly_payments) if member.monthly_payments is not None else "",
        amount_cell(member.part_time_percent) if member.part_time_percent is not None else "",
    ]


def _csv(output: PartOutput, title: str, columns: tuple[str, ...], rows: list[list[str]]) -> None:
    out = [BOM, row_line([text_cell(title)]), row_line([text_cell(c) for c in columns])]
    out.extend(row_line(row) for row in rows)
    output.files.append(BundleFile(LIST_FILE, "".join(out).encode("utf-8")))
    output.entries.append(IndexEntry(LIST_FILE, len(rows), "rows"))


def build_payslips(ctx: PartContext) -> PartOutput:
    """The accepted payslips of the months in the period, one row each with the figures the
    payslip printed, and the payslip's own file when it came through the inbox. A payslip a
    person typed has no file: it is listed as missing, not left out."""
    assert ctx.legal_entity is not None
    output = PartOutput()
    rows: list[list[str]] = []
    found = ctx.session.execute(
        select(StaffPayslip, StaffMember.name)
        .join(StaffMember, StaffMember.id == StaffPayslip.staff_member_id)
        .where(
            StaffPayslip.legal_entity_id == ctx.legal_entity.id,
            StaffPayslip.period >= ctx.period.date_from.replace(day=1),
            StaffPayslip.period <= ctx.period.date_to,
        )
        .order_by(StaffPayslip.period, StaffMember.name, StaffPayslip.id)
    )
    for slip, name in found:
        file_name = ""
        item = ctx.session.get(InboxItem, slip.inbox_item_id) if slip.inbox_item_id else None
        label = f"{slip.period:%Y-%m} {name}"
        if item is None or item.storage_key is None:
            output.missing.append(MissingFile(label, "Cedolino", "nessun file del cedolino"))
        else:
            file_name = f"{slip.period:%Y-%m}-{slip.id.hex[-8:]}-{basename(item.original_name)}"
            output.files.append(ctx.attach(file_name, ctx.storage.get(item.storage_key)))
        rows.append(
            [
                f"{slip.period:%Y-%m}",
                text_cell(name),
                amount_cell(slip.gross),
                amount_cell(slip.net),
                amount_cell(slip.withholding),
                text_cell(file_name),
            ]
        )
    _csv(output, "Cedolini del periodo", PAYSLIP_COLUMNS, rows)
    output.entries.append(IndexEntry("", len(output.files) - 1, "files"))
    return output


def build_hires(ctx: PartContext) -> PartOutput:
    output = PartOutput()
    members = ctx.session.scalars(
        _members(ctx)
        .where(
            StaffMember.start_date >= ctx.period.date_from,
            StaffMember.start_date <= ctx.period.date_to,
        )
        .order_by(StaffMember.start_date, StaffMember.name, StaffMember.id)
    )
    rows = [[text_cell(m.name), m.start_date.isoformat(), *_terms(m)] for m in members]
    _csv(output, "Assunzioni del periodo", HIRE_COLUMNS, rows)
    return output


def build_leavers(ctx: PartContext) -> PartOutput:
    output = PartOutput()
    members = ctx.session.scalars(
        _members(ctx)
        .where(
            StaffMember.end_date.is_not(None),
            StaffMember.end_date >= ctx.period.date_from,
            StaffMember.end_date <= ctx.period.date_to,
        )
        .order_by(StaffMember.end_date, StaffMember.name, StaffMember.id)
    )
    rows = [
        [text_cell(m.name), text_cell(KIND_WORDS[m.kind]), m.end_date.isoformat()]
        for m in members
        if m.end_date is not None
    ]
    _csv(output, "Cessazioni del periodo", LEAVER_COLUMNS, rows)
    return output


def build_pay_changes(ctx: PartContext) -> PartOutput:
    """People whose record was edited in the period and who were not hired in it, with their
    terms as they stand and the day of the last edit."""
    assert ctx.legal_entity is not None
    output = PartOutput()
    day = cast(func.timezone(ITALY_TZ.key, Activity.occurred_at), Date)
    edited = dict(
        ctx.session.execute(
            select(Activity.entity_id, func.max(day))
            .where(
                Activity.entity_type == STAFF_ENTITY,
                Activity.kind == UPDATED_KIND,
                day >= ctx.period.date_from,
                day <= ctx.period.date_to,
            )
            .group_by(Activity.entity_id)
        ).all()
    )
    rows: list[list[str]] = []
    members = ctx.session.scalars(
        _members(ctx)
        .where(StaffMember.id.in_(edited.keys()))
        .order_by(StaffMember.name, StaffMember.id)
    )
    for member in members:
        if ctx.period.date_from <= member.start_date <= ctx.period.date_to:
            continue
        last: dt.date = edited[member.id]
        rows.append(
            [text_cell(member.name), last.isoformat(), *_terms(member), text_cell(CHANGE_NOTE)]
        )
    _csv(output, "Variazioni di retribuzione del periodo", CHANGE_COLUMNS, rows)
    return output
