"""The framework agreement's twelve months, and the letters' numbers (REB-387).

Article 9.1: the agreement lasts twelve months from its signature and renews itself
for twelve more unless either party gives notice thirty days before the end. For the
hub it is active from its signature until someone records a notice or a withdrawal
(spec § 1d); the next renewal and the last day for a notice are computed here and never
stored. Dates are Rome's, where rebase signs: a signature at 23:30 UTC on 30 September
is dated 1 October.
"""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import ContractDocumentRead
from rebase_core.models import ContractDocument, LetterCounter

ROME = ZoneInfo("Europe/Rome")
NOTICE_DAYS = 30
QUADRO = "quadro"


def rome_today() -> date:
    return datetime.now(ROME).date()


def anniversary(signed_on: date, year: int) -> date:
    """`signed_on` in `year`; 29 February falls on the 28th in a common year."""
    try:
        return signed_on.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def next_renewal(signed_on: date, today: date) -> date:
    """The first anniversary of the signature after `today`: on the anniversary itself
    the new twelve months have already started."""
    renewal = anniversary(signed_on, today.year)
    if renewal <= today:
        renewal = anniversary(signed_on, today.year + 1)
    return renewal


def last_notice_day(renewal: date) -> date:
    return renewal - timedelta(days=NOTICE_DAYS)


def signed_on(document: ContractDocument) -> date | None:
    return document.signed_at.astimezone(ROME).date() if document.signed_at is not None else None


def is_active(document: ContractDocument) -> bool:
    return document.kind == QUADRO and document.stato == "firmato" and document.notice_at is None


def active_framework(session: Session, freelancer_id: UUID) -> ContractDocument | None:
    return session.scalars(
        select(ContractDocument)
        .where(
            ContractDocument.kind == QUADRO,
            ContractDocument.freelancer_id == freelancer_id,
            ContractDocument.stato == "firmato",
            ContractDocument.notice_at.is_(None),
        )
        .order_by(ContractDocument.signed_at.desc(), ContractDocument.created_at.desc())
        .limit(1)
    ).first()


def pending_framework(session: Session, freelancer_id: UUID) -> ContractDocument | None:
    """A framework agreement written and not signed yet: the one out for signature
    (`inviato`, phase 3) when there is one, else the newest merely generated."""
    pending = list(
        session.scalars(
            select(ContractDocument)
            .where(
                ContractDocument.kind == QUADRO,
                ContractDocument.freelancer_id == freelancer_id,
                ContractDocument.stato.in_(("generato", "inviato")),
            )
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        )
    )
    for document in pending:
        if document.stato == "inviato":
            return document
    return pending[0] if pending else None


def next_letter_number(session: Session, year: int) -> str:
    """The next letter number of `year`, `YYYY-NNN`, taken inside the caller's
    transaction and never committed here: a generation that fails rolls it back, and a
    second transaction asking at the same moment waits on this row, then takes the next."""
    taken = session.execute(
        pg_insert(LetterCounter)
        .values(anno=year, ultimo=1)
        .on_conflict_do_update(
            index_elements=[LetterCounter.anno], set_={"ultimo": LetterCounter.ultimo + 1}
        )
        .returning(LetterCounter.ultimo)
    ).scalar_one()
    return f"{year}-{taken:03d}"


def document_read(
    document: ContractDocument, today: date, current_version: str
) -> ContractDocumentRead:
    active = is_active(document)
    signed = signed_on(document)
    renewal = next_renewal(signed, today) if active and signed is not None else None
    return ContractDocumentRead(
        id=document.id,
        kind=document.kind,
        freelancer_id=document.freelancer_id,
        match_id=document.match_id,
        numero=document.numero,
        text_version=document.text_version,
        testo_bozza=document.testo_bozza,
        stato=document.stato,
        created_at=document.created_at,
        created_by=document.created_by,
        sent_at=document.sent_at,
        signed_at=document.signed_at,
        notice_at=document.notice_at,
        cancel_reason=document.cancel_reason,
        ha_pdf_firmato=document.signed_pdf is not None,
        attivo=active,
        rinnovo=renewal,
        ultimo_giorno_disdetta=last_notice_day(renewal) if renewal is not None else None,
        nuova_versione=document.kind == QUADRO and document.text_version != current_version,
    )
