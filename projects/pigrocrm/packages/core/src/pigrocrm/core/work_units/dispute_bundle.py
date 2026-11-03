"""The dispute bundle of one day (REB-759): a ZIP a person can hand to the other side, or to a
lawyer, that holds what an argument about a contested day needs, written out so it survives
outside the app. Mastro's `dispute-bundle` (`summary.ts`, `zip.ts`), on this schema.

- `summary.txt`: the day, the approval and the sentence that approves, the day's own log, the
  month's register of the contract's days, and the invoice line the day landed on. Dates are
  ISO strings and amounts plain decimals with their currency, so a figure a client cross-checks
  never depends on who reads it.
- `source/<file>`: the archived original the approval rests on (the email, the PDF), byte for
  byte. A summary of the source document is not the source document, so a bundle whose
  approval names one carries its bytes; a document whose file cannot be read is said so in the
  summary, never left out in silence.

Every name in the archive is made safe for any filesystem: the original's file name can trace
back to a sender's header, and `zipfile` writes a key byte for byte.

Not a `*Service`: it is a read that returns bytes, like the year's CSV, and an MCP tool returns
no bytes. A person downloads it from the day's page.
"""

import hashlib
import io
import re
import zipfile
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.labels import APPROVAL_CHANNEL, INVOICE_STATUS, WORK_UNIT_STATUS, label
from pigrocrm.core.money import round_money
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.work_units.models import Approval, WorkUnit
from pigrocrm.core.work_units.schemas import WorkUnitDetail
from pigrocrm.core.work_units.service import WorkUnitService

MAX_ENTRY_NAME_LENGTH = 200
# What a bundle may carry of the archived original, since the ZIP is built in memory.
SOURCE_MAX_BYTES = 25 * 1024 * 1024
# What a ZIP entry's timestamp can hold, fixed: the bundle of a day is the same bytes however
# often it is downloaded, which is what lets a person compare two copies by hash.
ENTRY_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
# The days a register of the month counts: committed, worked or billed. A proposal, a day
# worked without the approval its contract asks for and a day written off are not in a
# total a client will cross-check; they are listed, marked, and left out of the sum.
COUNTED = ("approved", "worked", "disputed", "invoiced", "paid")
NEVER_HAPPENED = ("rejected", "revoked")


def safe_entry_name(name: str, fallback: str) -> str:
    """One flat, filesystem-safe file name from `name`.

    `/` and `\\` (the archive is opened on whatever OS the person runs), `.`/`..`-only
    segments, empty segments (a leading separator, i.e. an absolute path), Windows drive
    letters and control characters are all dropped, and what survives is joined into a single
    component: nothing this returns contains a separator, so nothing can address a path
    outside the directory it is put under (CWE-22, zip-slip). `fallback` is always a name this
    process built, never one a sender supplied, and covers the case nothing usable survives.
    """
    segments = []
    for raw in re.split(r"[/\\]+", name):
        bare = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip()
        if re.fullmatch(r"[A-Za-z]:", bare):
            continue  # a drive letter
        # The characters Windows refuses in a name go; a trailing dot or space too, which
        # Windows silently drops.
        segment = re.sub(r'[<>:"|?*]', "", bare).rstrip(". ")
        if segment == "" or re.fullmatch(r"\.+", segment):
            continue
        segments.append(segment)
    flat = "-".join(segments)[:MAX_ENTRY_NAME_LENGTH]
    return flat or fallback


def _section(heading: str, lines: list[str]) -> list[str]:
    return [heading, *(f"  {line}" for line in lines), ""]


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _actor_label(actor: dict[str, object]) -> str:
    """Who acted, as stored: the kind and, when there is one, the id of the person or token."""
    kind = str(actor.get("kind", "sistema"))
    return f"{kind} {actor['id']}" if actor.get("id") else kind


def _approval_reference(detail: WorkUnitDetail) -> list[str]:
    approval = detail.approval
    if approval is None:
        return ["Nessuna approvazione in archivio."]
    lines = [
        f"Canale: {label(APPROVAL_CHANNEL, approval.channel)}",
        f"Mittente: {approval.sender}",
        f"Ricevuta il: {_iso(approval.received_at)}",
    ]
    if approval.message_id:
        lines.append(f"Message-ID: {approval.message_id}")
    lines.append(f'Estratto: "{approval.excerpt}"')
    return lines


def _proof_label(unit: WorkUnit, approvals: dict[UUID, Approval]) -> str:
    """The approval a day rests on, as a person reads it: channel, sender, date."""
    if unit.approval_id is None:
        return "senza approvazione"
    approval = approvals.get(unit.approval_id)
    if approval is None:
        return "approvazione non leggibile"
    return (
        f"approvazione {label(APPROVAL_CHANNEL, approval.channel)} di {approval.sender}, "
        f"{approval.received_at.date()}"
    )


def _register_lines(session: Session, detail: WorkUnitDetail) -> tuple[str, str, list[str]]:
    """The contract's days in the month of the day, the window a register of approvals is read
    in, each with the state it is in and the approval it rests on."""
    first, end = month_bounds(detail.date.year, detail.date.month)
    units = list(
        session.execute(
            select(WorkUnit)
            .where(
                WorkUnit.contract_id == detail.contract_id,
                WorkUnit.date >= first,
                WorkUnit.date <= end,
                WorkUnit.status.not_in(NEVER_HAPPENED),
            )
            .order_by(WorkUnit.date, WorkUnit.id)
        ).scalars()
    )
    approval_ids = {unit.approval_id for unit in units if unit.approval_id is not None}
    approvals = (
        {
            approval.id: approval
            for approval in session.scalars(select(Approval).where(Approval.id.in_(approval_ids)))
        }
        if approval_ids
        else {}
    )
    lines: list[str] = []
    total = Decimal("0.00")
    for unit in units:
        if unit.status in COUNTED:
            total += unit.quantity
        mark = " <- questa giornata" if unit.id == detail.id else ""
        proof = _proof_label(unit, approvals)
        lines.append(
            f"{unit.date.isoformat()}  {unit.quantity}  {label(WORK_UNIT_STATUS, unit.status)}  "
            f"({proof}){mark}"
        )
    lines.append(
        f"Totale giornate del mese: {total} (approvate, lavorate, contestate, fatturate, pagate)"
    )
    return first.isoformat(), end.isoformat(), lines


def _invoice_lines(session: Session, detail: WorkUnitDetail) -> list[str]:
    if detail.invoice_line_id is None:
        return ["Nessuna riga di fattura: la giornata non è stata fatturata."]
    row = session.execute(
        select(InvoiceLine, Invoice)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(InvoiceLine.id == detail.invoice_line_id)
    ).first()
    if row is None:
        return ["La riga di fattura non è più leggibile."]
    line, invoice = row
    number = f"{invoice.year}/{invoice.number}" if invoice.number is not None else "bozza"
    issued = f", emessa il {invoice.issue_date.isoformat()}" if invoice.issue_date else ""
    return [
        f"Fattura {number} ({label(INVOICE_STATUS, invoice.status)}{issued})",
        f"Riga {line.line_number}: {line.description}",
        f"Importo della riga: {round_money(line.total_price)} EUR",
    ]


def render_summary(session: Session, detail: WorkUnitDetail, source_line: str) -> str:
    first, last, register = _register_lines(session, detail)
    parts: list[str] = [
        f"FASCICOLO DELLA GIORNATA {detail.date.isoformat()}",
        "",
        *_section(
            "Giornata",
            [
                f"Contratto: {detail.contract_title}",
                f"Cliente: {detail.customer_name}",
                f"Descrizione: {detail.description}",
                f"Quantità: {detail.quantity}",
                f"Stato: {label(WORK_UNIT_STATUS, detail.status)}",
                *([f"Note: {detail.note}"] if detail.note else []),
            ],
        ),
        *_section("Approvazione", _approval_reference(detail)),
        *_section("Documento originale", [source_line]),
        *_section(
            "Storico della giornata",
            [
                f"{_iso(t.created_at)}  {label(WORK_UNIT_STATUS, t.previous_status) or '-'} -> "
                f"{label(WORK_UNIT_STATUS, t.new_status)}  "
                f"({_actor_label(t.actor)})  {t.reason}"
                for t in detail.transitions
            ],
        ),
        *_section(f"Registro del contratto, da {first} a {last}", register),
        *_section("Riga di fattura", _invoice_lines(session, detail)),
    ]
    return "\n".join(parts).rstrip("\n") + "\n"


def _refuse_oversized_source(session: Session, document: Document, title: str) -> None:
    """The bundle is built in memory, so the archived original it carries is bounded: a
    larger file is refused by name before it is read, not truncated and not left out."""
    size = session.scalar(
        select(DocumentVersion.size).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.number == document.current_version,
        )
    )
    if size is not None and size > SOURCE_MAX_BYTES:
        raise ValidationFailed(
            "dispute_bundle",
            "document_id",
            f"il file originale di «{title}» pesa troppo per un fascicolo: scaricalo dal documento",
            expected=f"un originale fino a {SOURCE_MAX_BYTES // (1024 * 1024)} MiB",
        )


def build_dispute_bundle(
    session: Session, storage: DocumentStorage, work_unit_id: UUID, actor: Actor
) -> tuple[str, bytes]:
    """`(filename, zip bytes)` for one day. Read-only, so open to every role; what an actor
    may see is the row-level scope's, exactly as for the day's own page."""
    detail = WorkUnitService(session).detail(work_unit_id, actor)
    files: dict[str, bytes] = {}
    source_line = "Nessun documento in archivio: la giornata non ha un'approvazione."
    approval = detail.approval
    if approval is not None:
        document = session.get(Document, approval.document_id)
        title = document.title if document is not None else str(approval.document_id)
        if document is not None:
            _refuse_oversized_source(session, document, title)
        try:
            content, _, filename = DocumentService(session, storage).download(
                approval.document_id, None, actor
            )
        except (NotFound, Conflict, ValidationFailed, OSError):
            source_line = f"Il file originale di «{title}» non è leggibile dall'archivio."
        else:
            entry = safe_entry_name(filename, f"document-{approval.document_id}")
            files[f"source/{entry}"] = content
            digest = hashlib.sha256(content).hexdigest()
            source_line = f"source/{entry}: «{title}», versione corrente, SHA-256 {digest}"
    files = {"summary.txt": render_summary(session, detail, source_line).encode(), **files}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=ENTRY_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return (
        f"fascicolo-giornata-{detail.date.isoformat()}-{str(detail.id)[:8]}.zip",
        buffer.getvalue(),
    )


__all__ = ["build_dispute_bundle", "render_summary", "safe_entry_name"]
