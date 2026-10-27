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
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.money import round_money
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.work_units.models import Approval, WorkUnit
from pigrocrm.core.work_units.schemas import WorkUnitDetail
from pigrocrm.core.work_units.service import WorkUnitService

MAX_ENTRY_NAME_LENGTH = 200
# What a ZIP entry's timestamp can hold, fixed: the bundle of a day is the same bytes however
# often it is downloaded, which is what lets a person compare two copies by hash.
ENTRY_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
# The days a register of the month counts: committed, worked or billed. A proposal, a day
# worked without the approval its contract asks for and a day written off are not in a
# total a client will cross-check; they are listed, marked, and left out of the sum.
COUNTED = ("approvato", "lavorato", "contestato", "fatturato", "pagato")
NEVER_HAPPENED = ("rifiutato", "revocato")


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
        segment = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip()
        if segment == "" or re.fullmatch(r"\.+", segment) or re.fullmatch(r"[A-Za-z]:", segment):
            continue
        segments.append(segment)
    flat = "-".join(segments)[:MAX_ENTRY_NAME_LENGTH]
    return flat or fallback


def _section(heading: str, lines: list[str]) -> list[str]:
    return [heading, *(f"  {line}" for line in lines), ""]


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _actor_label(attore: dict[str, object]) -> str:
    """Who acted, as stored: the kind and, when there is one, the id of the person or token."""
    kind = str(attore.get("kind", "sistema"))
    return f"{kind} {attore['id']}" if attore.get("id") else kind


def _approval_reference(detail: WorkUnitDetail) -> list[str]:
    approval = detail.approval
    if approval is None:
        return ["Nessuna approvazione in archivio."]
    lines = [
        f"Canale: {approval.canale}",
        f"Mittente: {approval.mittente}",
        f"Ricevuta il: {_iso(approval.ricevuto_il)}",
    ]
    if approval.message_id:
        lines.append(f"Message-ID: {approval.message_id}")
    lines.append(f'Estratto: "{approval.estratto}"')
    return lines


def _proof_label(session: Session, unit: WorkUnit) -> str:
    """The approval a day rests on, as a person reads it: channel, sender, date."""
    if unit.approval_id is None:
        return "senza approvazione"
    approval = session.get(Approval, unit.approval_id)
    if approval is None:
        return "approvazione non leggibile"
    return f"approvazione {approval.canale} di {approval.mittente}, {approval.ricevuto_il.date()}"


def _register_lines(session: Session, detail: WorkUnitDetail) -> tuple[str, str, list[str]]:
    """The contract's days in the month of the day, the window a register of approvals is read
    in, each with the state it is in and the approval it rests on."""
    first = detail.data.replace(day=1)
    last = date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)
    rows = session.execute(
        select(WorkUnit)
        .where(
            WorkUnit.contract_id == detail.contract_id,
            WorkUnit.data >= first,
            WorkUnit.data < last,
            WorkUnit.stato.not_in(NEVER_HAPPENED),
        )
        .order_by(WorkUnit.data, WorkUnit.id)
    ).scalars()
    lines: list[str] = []
    total = Decimal("0.00")
    for unit in rows:
        if unit.stato in COUNTED:
            total += unit.quantita
        mark = " <- questa giornata" if unit.id == detail.id else ""
        proof = _proof_label(session, unit)
        lines.append(f"{unit.data.isoformat()}  {unit.quantita}  {unit.stato}  ({proof}){mark}")
    lines.append(
        f"Totale giornate del mese: {total} (approvate, lavorate, contestate, fatturate, pagate)"
    )
    end = date.fromordinal(last.toordinal() - 1)
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
    number = f"{invoice.anno}/{invoice.numero}" if invoice.numero is not None else "bozza"
    issued = f", emessa il {invoice.data_emissione.isoformat()}" if invoice.data_emissione else ""
    return [
        f"Fattura {number} ({invoice.stato}{issued})",
        f"Riga {line.numero_linea}: {line.descrizione}",
        f"Importo della riga: {round_money(line.prezzo_totale)} EUR",
    ]


def render_summary(session: Session, detail: WorkUnitDetail, source_line: str) -> str:
    first, last, register = _register_lines(session, detail)
    parts: list[str] = [
        f"FASCICOLO DELLA GIORNATA {detail.data.isoformat()}",
        "",
        *_section(
            "Giornata",
            [
                f"Contratto: {detail.contract_titolo}",
                f"Cliente: {detail.customer_nome}",
                f"Descrizione: {detail.descrizione}",
                f"Quantità: {detail.quantita}",
                f"Stato: {detail.stato}",
                *([f"Note: {detail.note}"] if detail.note else []),
            ],
        ),
        *_section("Approvazione", _approval_reference(detail)),
        *_section("Documento originale", [source_line]),
        *_section(
            "Storico della giornata",
            [
                f"{_iso(t.created_at)}  {t.stato_precedente or '-'} -> {t.stato_nuovo}  "
                f"({_actor_label(t.attore)})  {t.motivo}"
                for t in detail.transizioni
            ],
        ),
        *_section(f"Registro del contratto, da {first} a {last}", register),
        *_section("Riga di fattura", _invoice_lines(session, detail)),
    ]
    return "\n".join(parts).rstrip("\n") + "\n"


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
        title = document.titolo if document is not None else str(approval.document_id)
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
        f"fascicolo-giornata-{detail.data.isoformat()}-{str(detail.id)[:8]}.zip",
        buffer.getvalue(),
    )


__all__ = ["build_dispute_bundle", "render_summary", "safe_entry_name"]
