"""The framework agreement's twelve months, and the letters' numbers (REB-387).

Article 9.1: the agreement lasts twelve months from its signature and renews itself
for twelve more unless either party gives notice thirty days before the end. For the
hub it is active from its signature until someone records a notice or a withdrawal
(spec § 1d); the next renewal and the last day for a notice are computed here and never
stored. Dates are Rome's, where rebase signs: a signature at 23:30 UTC on 30 September
is dated 1 October.
"""

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import ContractDocumentRead
from rebase_core.contracts.render import text_version
from rebase_core.match_words import DocumentFacts, document_words
from rebase_core.models import ContractDocument, LetterCounter
from rebase_core.regimes import as_regime, document_label, text_name

ROME = ZoneInfo("Europe/Rome")
NOTICE_DAYS = 30
FRAMEWORK = "framework"


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


def rome_day(moment: datetime | None) -> date | None:
    return moment.astimezone(ROME).date() if moment is not None else None


def signed_on(document: ContractDocument) -> date | None:
    return rome_day(document.signed_at)


def is_active(document: ContractDocument) -> bool:
    return document.kind == FRAMEWORK and document.status == "signed" and document.notice_at is None


def framework_dates(
    document: ContractDocument, today: date
) -> tuple[bool, date | None, date | None]:
    """`(attivo, rinnovo, ultimo_giorno_disdetta)`: the one arithmetic the admin's
    `document_read` and the member area's own read (REB-392) share, so a change to the
    twelve months' math lands once."""
    active = is_active(document)
    signed = signed_on(document)
    renewal = next_renewal(signed, today) if active and signed is not None else None
    return active, renewal, last_notice_day(renewal) if renewal is not None else None


def active_framework(session: Session, freelancer_id: UUID, regime: str) -> ContractDocument | None:
    """The freelancer's active framework agreement of `regime` (REB-687): a freelancer
    may hold one of each regime, and neither stands in for the other."""
    return session.scalars(
        select(ContractDocument)
        .where(
            ContractDocument.kind == FRAMEWORK,
            ContractDocument.freelancer_id == freelancer_id,
            ContractDocument.regime == regime,
            ContractDocument.status == "signed",
            ContractDocument.notice_at.is_(None),
        )
        .order_by(ContractDocument.signed_at.desc(), ContractDocument.created_at.desc())
        .limit(1)
    ).first()


def pending_framework(
    session: Session, freelancer_id: UUID, regime: str
) -> ContractDocument | None:
    """A framework agreement of `regime` written and not signed yet: the one out for
    signature (`sent`, phase 3) when there is one, else the newest merely generated.
    One of the other regime never counts (REB-687)."""
    pending = list(
        session.scalars(
            select(ContractDocument)
            .where(
                ContractDocument.kind == FRAMEWORK,
                ContractDocument.freelancer_id == freelancer_id,
                ContractDocument.regime == regime,
                ContractDocument.status.in_(("generated", "sent")),
            )
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        )
    )
    for document in pending:
        if document.status == "sent":
            return document
    return pending[0] if pending else None


# Which framework agreement a waiting letter reads, when a freelancer has several: an
# active one first, then one out for signature, then one merely generated.
_FRAMEWORK_RANK = {"signed": 0, "sent": 1, "generated": 2}


def _standing(documents: Iterable[tuple[str, datetime | None]]) -> str | None:
    """The best-ranked of `(stato, notice_at)`: a signed one counts only while active
    (no notice recorded, as `is_active` reads it); a cancelled one, or one given notice,
    not at all."""
    counted = [
        status
        for status, notice_at in documents
        if status in _FRAMEWORK_RANK and (status != "signed" or notice_at is None)
    ]
    return min(counted, key=_FRAMEWORK_RANK.__getitem__, default=None)


def framework_state(frameworks: Iterable[ContractDocument], regime: str) -> str | None:
    """Where one freelancer's framework agreement of `regime` stands, from the rows
    already read: `signed` for an active one (`active_framework`), else
    `pending_framework`'s state, `sent` or `generated`, else `None`. A match reads the
    one of its letter's regime (REB-687)."""
    return _standing(
        (document.status, document.notice_at)
        for document in frameworks
        if document.regime == regime
    )


def framework_states(
    session: Session, freelancer_ids: Iterable[UUID]
) -> dict[tuple[UUID, str], str]:
    """`framework_state` for many freelancers in one query, keyed by (freelancer,
    regime), so the «Match» list reads one statement for its page rather than one per
    row. A pair with none that counts is absent."""
    ids = set(freelancer_ids)
    if not ids:
        return {}
    documents: dict[tuple[UUID, str], list[tuple[str, datetime | None]]] = {}
    for freelancer_id, regime, status, notice_at in session.execute(
        select(
            ContractDocument.freelancer_id,
            ContractDocument.regime,
            ContractDocument.status,
            ContractDocument.notice_at,
        ).where(
            ContractDocument.kind == FRAMEWORK,
            ContractDocument.freelancer_id.in_(ids),
            ContractDocument.status.in_(tuple(_FRAMEWORK_RANK)),
        )
    ):
        documents.setdefault((freelancer_id, regime), []).append((status, notice_at))
    return {
        key: standing
        for key, pairs in documents.items()
        if (standing := _standing(pairs)) is not None
    }


def current_framework_version(regime: str) -> str:
    """The `version` of `regime`'s framework agreement text as it is in the package
    today: what a quadro's own `text_version` is compared with for `new_version`,
    each against its own text (REB-687)."""
    return text_version(text_name(FRAMEWORK, regime))


def next_letter_number(session: Session, year: int) -> str:
    """The next letter number of `year`, `YYYY-NNN`, taken inside the caller's
    transaction and never committed here: a generation that fails rolls it back, and a
    second transaction asking at the same moment waits on this row, then takes the next."""
    taken = session.execute(
        pg_insert(LetterCounter)
        .values(year=year, last=1)
        .on_conflict_do_update(
            index_elements=[LetterCounter.year], set_={"last": LetterCounter.last + 1}
        )
        .returning(LetterCounter.last)
    ).scalar_one()
    return f"{year}-{taken:03d}"


def document_facts(document: ContractDocument, today: date) -> DocumentFacts:
    """What `match_words` reads of a document, each date a day in Rome."""
    active, renewal, last_notice = framework_dates(document, today)
    return DocumentFacts(
        kind=document.kind,
        status=document.status,
        number=document.number,
        draft_text=bool(document.draft_text),
        sent_on=rome_day(document.sent_at),
        signed_on=signed_on(document),
        notice_on=rome_day(document.notice_at),
        cancel_reason=document.cancel_reason,
        has_pdf_signed=document.signed_pdf is not None,
        active=active,
        renewal=renewal,
        last_notice_day=last_notice,
    )


def document_read(
    document: ContractDocument,
    today: date,
    current_version: str,
    facts: DocumentFacts | None = None,
) -> ContractDocumentRead:
    """`facts`, when the caller already built them from `document` for the same `today`,
    are not built again."""
    facts = facts or document_facts(document, today)
    situation, next_action, other_actions = document_words(facts)
    return ContractDocumentRead(
        id=document.id,
        kind=document.kind,
        regime=as_regime(document.regime),
        tipo=document_label(document.kind, document.regime),
        freelancer_id=document.freelancer_id,
        match_id=document.match_id,
        number=document.number,
        text_version=document.text_version,
        draft_text=document.draft_text,
        status=document.status,
        created_at=document.created_at,
        created_by=document.created_by,
        sent_at=document.sent_at,
        signed_at=document.signed_at,
        notice_at=document.notice_at,
        cancel_reason=document.cancel_reason,
        has_pdf_signed=facts.has_pdf_signed,
        active=facts.active,
        renewal=facts.renewal,
        last_notice_day=facts.last_notice_day,
        new_version=document.kind == FRAMEWORK and document.text_version != current_version,
        situation=situation,
        next_action=next_action,
        other_actions=other_actions,
    )
