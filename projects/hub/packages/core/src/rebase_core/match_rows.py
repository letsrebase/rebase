"""The rows a match's documents are built from, and the locks every write takes (REB-892).

The shared ground of `MatchService` (`rebase_core.matches`) and its parts, the reads
(`match_reads`), the drafts (`match_drafts`) and the documents' data (`match_documents`),
and of `SigningService` and `EngagementService`, which write the same rows.

**The lock order** is global: the freelancer's row (`lock_freelancer`), then the match
(`lock_match`), then its documents, and for a request the freelancer's row first and the
request's after, so two writers can never wait on each other. The caller of `lock_match`
must already hold the freelancer's row.

The lookups below read with `populate_existing`: the session keeps its objects across
commits (`expire_on_commit=False`), so a card, a request or tax data it still holds from an
earlier read would otherwise come back as they were then, not as `create` must see them
under its lock.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.framework import active_framework, pending_framework
from rebase_core.match_words import ORDINARY_NEEDS_PARTITA_IVA, FrameworkStep
from rebase_core.models import Company, ContractDocument, Freelancer, FreelancerFiscal, Match, User
from rebase_core.regimes import PARTITA_IVA

ENTITY = "match"
DOCUMENT_ENTITY = "documento"
QUADRO, LETTERA = "quadro", "lettera"


def full_name(user: User) -> str:
    return f"{user.nome} {user.cognome}".strip()


def require_regime_fits(partita_iva: str | None, regime: str) -> None:
    """An ordinary letter prints the freelancer's partita IVA, so it needs saved tax data
    with one (REB-687). An occasional letter never prints one and is allowed either way:
    the check warns a partita IVA holder (`match_words.HAS_PARTITA_IVA`). `check`,
    `preview` and `create` refuse on `regime`, the field «Tipo di lettera» sets."""
    if regime == PARTITA_IVA and partita_iva is None:
        raise ValidationFailed(ENTITY, "regime", ORDINARY_NEEDS_PARTITA_IVA)


def freelancer_row(session: Session, freelancer_id: UUID) -> tuple[Freelancer, User]:
    row = session.execute(
        select(Freelancer, User)
        .join(User, User.id == Freelancer.user_id)
        .where(Freelancer.id == freelancer_id)
        .execution_options(populate_existing=True)
    ).first()
    if row is None or row[0].deleted_at is not None:
        raise NotFound("freelancer", freelancer_id)
    return row[0], row[1]


def company_row(session: Session, company_id: UUID, *, lock: bool = False) -> tuple[Company, User]:
    stmt = (
        select(Company, User)
        .join(User, User.id == Company.user_id)
        .where(Company.id == company_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        stmt = stmt.with_for_update(read=True, of=Company)
    row = session.execute(stmt).first()
    if row is None or row[0].deleted_at is not None:
        raise NotFound("company", company_id)
    return row[0], row[1]


def matchable_company(
    session: Session, company_id: UUID, *, lock: bool = False
) -> tuple[Company, User]:
    company, referente = company_row(session, company_id, lock=lock)
    if company.stato == "chiuso":
        raise ValidationFailed(ENTITY, "company_id", "la richiesta è chiusa: riaprila prima")
    return company, referente


def fiscal_row(session: Session, freelancer_id: UUID) -> FreelancerFiscal:
    row = session.scalar(
        select(FreelancerFiscal)
        .where(FreelancerFiscal.freelancer_id == freelancer_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValidationFailed(ENTITY, "fiscale", "mancano i dati fiscali del freelance")
    return row


def framework_step(
    session: Session, freelancer_id: UUID, regime: str
) -> tuple[ContractDocument | None, FrameworkStep]:
    """The active framework agreement of `regime`, and where the next match's stands:
    `attivo`, `in_firma` when one is out for signature (the letter waits for it), else
    `da_inviare` (a new one leaves first, or the one generated for an earlier draft)."""
    active = active_framework(session, freelancer_id, regime)
    if active is not None:
        return active, "attivo"
    pending = pending_framework(session, freelancer_id, regime)
    out = pending is not None and pending.stato == "inviato"
    return None, "in_firma" if out else "da_inviare"


def match_freelancer(session: Session, match_id: UUID) -> UUID:
    """The freelancer a match belongs to, read with no lock of its own -- only to know
    which row `lock_freelancer` must take next, the first step of the global lock order
    every write of a match follows (REB-407)."""
    freelancer_id = session.scalar(select(Match.freelancer_id).where(Match.id == match_id))
    if freelancer_id is None:
        raise NotFound(ENTITY, match_id)
    return freelancer_id


def lock_freelancer(session: Session, freelancer_id: UUID) -> None:
    """The freelancer's row, locked until this transaction ends: the first step of the
    global lock order, which every other lock of a match's writes assumes the caller
    already took (REB-407)."""
    session.execute(select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update())


def lock_match(session: Session, match_id: UUID) -> Match:
    """The match, row-locked until this transaction ends, and read again from the
    database rather than from the session's memory. The caller must already hold the
    freelancer's row lock, the global order (REB-407)."""
    match = session.scalars(
        select(Match)
        .where(Match.id == match_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if match is None:
        raise NotFound(ENTITY, match_id)
    return match
