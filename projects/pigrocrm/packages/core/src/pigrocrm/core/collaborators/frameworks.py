"""The framework agreement's twelve months and which one stands (REB-1019, moved from the hub's
`framework.py`, REB-387 and REB-687).

Article 9.1: the agreement lasts twelve months from its signature and renews itself for twelve more
unless either party gives notice thirty days before the end. Here it is active from its signature
until someone records a notice or a withdrawal; the next renewal and the last day for a notice are
computed and never stored. Dates are the space's own (`Settings.timezone`, where the issuer signs):
a signature at 23:30 UTC on 30 September is dated 1 October in Rome.

A freelancer holds a framework agreement of each regime (`ordinary` and `occasional`) and neither
stands in for the other: a letter leaves only once the agreement of its own regime is signed.
"""

import datetime as dt
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.letters import FRAMEWORK_AGREEMENT
from pigrocrm.core.documents.models import Document

NOTICE_DAYS = 30


def local_day(moment: dt.datetime | None, zone: str) -> dt.date | None:
    """`moment` as a calendar day in `zone`."""
    return moment.astimezone(ZoneInfo(zone)).date() if moment is not None else None


def anniversary(signed_on: dt.date, year: int) -> dt.date:
    """`signed_on` in `year`; 29 February falls on the 28th in a common year."""
    try:
        return signed_on.replace(year=year)
    except ValueError:
        return dt.date(year, 2, 28)


def next_renewal(signed_on: dt.date, today: dt.date) -> dt.date:
    """The first anniversary of the signature after `today`: on the anniversary itself the new
    twelve months have already started."""
    renewal = anniversary(signed_on, today.year)
    if renewal <= today:
        renewal = anniversary(signed_on, today.year + 1)
    return renewal


def last_notice_day(renewal: dt.date) -> dt.date:
    return renewal - dt.timedelta(days=NOTICE_DAYS)


def is_active(document: Document) -> bool:
    """A framework agreement that is signed and has had no notice recorded."""
    return (
        document.type == FRAMEWORK_AGREEMENT
        and document.signature_state == "signed"
        and document.notice_at is None
    )


def active_framework(session: Session, freelancer_id: UUID, regime: str) -> Document | None:
    """The freelancer's active framework agreement of `regime`, the latest signed one."""
    return session.scalars(
        select(Document)
        .where(
            Document.type == FRAMEWORK_AGREEMENT,
            Document.customer_id == freelancer_id,
            Document.letter_type == regime,
            Document.deleted_at.is_(None),
            Document.signature_state == "signed",
            Document.notice_at.is_(None),
        )
        .order_by(Document.signed_at.desc(), Document.created_at.desc())
        .limit(1)
    ).first()


def pending_framework(session: Session, freelancer_id: UUID, regime: str) -> Document | None:
    """A framework agreement of `regime` written and not signed yet: the one out for signature
    (`sent`) when there is one, else the newest merely `generated`."""
    pending = list(
        session.scalars(
            select(Document)
            .where(
                Document.type == FRAMEWORK_AGREEMENT,
                Document.customer_id == freelancer_id,
                Document.letter_type == regime,
                Document.deleted_at.is_(None),
                Document.signature_state.in_(("generated", "sent")),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
    )
    for document in pending:
        if document.signature_state == "sent":
            return document
    return pending[0] if pending else None
