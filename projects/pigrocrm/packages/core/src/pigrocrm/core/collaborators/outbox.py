"""The notices the CRM owes the hub about an engagement, written where the engagement changes
(collaborators design § 5, «The hub's read model», REB-1106).

`NoticeOutbox.emit` is called inside the transaction of every change the hub must hear of (an
engagement created, a draft's fields changed, sent for signature, signed, cancelled, closed, a
framework notice served) and writes one `engagement_notices` row in it: the change and its notice
land together or not at all, which a send after the commit could not promise. The row carries the
engagement's own sequence number and **the whole read model as it was** (`payload`), so the hub's
upsert needs nothing else and a late delivery of an old row cannot overwrite a newer one: the hub
ignores a sequence older than the one it holds.

Only the space `PIGROCRM_LEADS_SPACE` names writes rows (a session says which space it serves in
`session.info`, set by the registry's factories and the cron walk; one that says nothing, a test's,
does not hold a notice back), since the hub reads no other space's engagements. An engagement whose
freelancer carries no hub id (one written in the CRM for somebody the hub never saw) has nothing to
tell the hub and writes no row. The row is never edited afterwards
except by the sweep that delivers it (`hub_notices.py`).
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators import letters
from pigrocrm.core.collaborators.models import NOTICE_KINDS, Engagement, EngagementNotice
from pigrocrm.core.collaborators.rules import is_leads_space
from pigrocrm.core.config import get_settings
from pigrocrm.core.db.scope import SPACE_SLUG_KEY
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.organizations.models import Organization

# The deal the hub's lead route files carries `rebase:request=<hub company id>` among its refs
# (`engagements.leads.lead_marker`, which a test holds this prefix to): that is how an engagement on
# the request's deal knows the hub company behind it.
REQUEST_REF_PREFIX = "rebase:request="

# The longest client name the hub's read model keeps (its column and its schema): a longer legal
# name is cut here, since the hub would refuse the whole notice for good (a 422) and the engagement
# would never reach it.
CLIENT_NAME_MAX = 200

# The engagement's columns the hub's read model shows: a draft that changes one of them tells the
# hub again (`updated`), since the read model would otherwise show the old value until the first
# send. `days_per_week` and the payment terms are not in it.
NOTICE_COLUMNS = frozenset(
    {
        "role",
        "starts_on",
        "ends_on",
        "fee",
        "fee_unit",
        "expected_days",
        "client_organization_id",
        "deal_id",
    }
)


def hub_company_of(refs: list[str]) -> UUID | None:
    """The hub company a deal's `rebase_refs` name, if one does and it is a well-formed id."""
    for ref in refs:
        if ref.startswith(REQUEST_REF_PREFIX):
            try:
                return UUID(ref.removeprefix(REQUEST_REF_PREFIX))
            except ValueError:
                return None
    return None


def _text(value: Decimal | dt.date | dt.datetime | UUID | None) -> str | None:
    """A value as JSON says it: a decimal as its digits (never a float), a date or an instant
    in ISO form, an id as its string."""
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def _hundredths(value: Decimal | None) -> str | None:
    """A figure to the cent, as the columns store it, whichever form the session holds it in."""
    return None if value is None else str(value.quantize(Decimal("0.01")))


class NoticeOutbox:
    def __init__(self, session: Session) -> None:
        self.session = session

    def emit(self, engagement: Engagement, kind: str) -> EngagementNotice | None:
        """Writes the engagement's next notice into the caller's transaction, flushed and never
        committed. `None` when the space is not the leads space (the only one whose engagements
        the hub reads, so no other space accumulates rows nobody delivers) and when the freelancer
        has no hub id: there is nobody to tell."""
        if kind not in NOTICE_KINDS:
            raise ValueError(f"unknown notice kind {kind!r}")
        if SPACE_SLUG_KEY in self.session.info and not is_leads_space(
            get_settings(), self.session.info[SPACE_SLUG_KEY]
        ):
            return None
        hub_freelancer_id = self.session.scalar(
            select(Organization.hub_freelancer_id).where(
                Organization.id == engagement.freelancer_id
            )
        )
        if hub_freelancer_id is None:
            return None
        # The engagement's own row is the lock that serialises its sequence: every writer of an
        # engagement already holds it, and this takes it for the one that does not (a new row, a
        # framework notice), so two notices can never draw the same number.
        self.session.execute(
            select(Engagement.id).where(Engagement.id == engagement.id).with_for_update()
        )
        sequence = (
            self.session.scalar(
                select(func.max(EngagementNotice.sequence)).where(
                    EngagementNotice.engagement_id == engagement.id
                )
            )
            or 0
        ) + 1
        notice = EngagementNotice(
            engagement_id=engagement.id,
            sequence=sequence,
            kind=kind,
            payload=self.payload(engagement, hub_freelancer_id),
        )
        self.session.add(notice)
        self.session.flush()
        return notice

    def payload(self, engagement: Engagement, hub_freelancer_id: UUID) -> dict[str, Any]:
        """The read model of design § 5: engagement id, hub freelancer id, hub company id when it
        is known, letter id and number, `signed_at`, mode, fee, expected days, role, start, end and
        state, and the client's name, which the hub needs to keep it out of the role a card prints
        (the one field beyond the design's list). **Never the client's price**: the engagement does
        not hold it."""
        letter = self.session.scalars(
            select(Document)
            .where(
                Document.engagement_id == engagement.id,
                Document.type == letters.ENGAGEMENT_LETTER,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(1)
        ).first()
        refs = (
            self.session.scalar(select(Deal.rebase_refs).where(Deal.id == engagement.deal_id))
            if engagement.deal_id is not None
            else None
        )
        client_name = (
            self.session.scalar(
                select(Organization.legal_name).where(
                    Organization.id == engagement.client_organization_id
                )
            )
            if engagement.client_organization_id is not None
            else None
        )
        signed = letter is not None and letter.signature_state == "signed"
        return {
            "engagement_id": str(engagement.id),
            "hub_match_id": _text(engagement.hub_match_id),
            "hub_freelancer_id": str(hub_freelancer_id),
            "hub_company_id": _text(hub_company_of(refs or [])),
            "client_name": client_name[:CLIENT_NAME_MAX] if client_name else client_name,
            "letter_id": _text(letter.id) if letter is not None else None,
            "letter_number": engagement.letter_number,
            "signed_at": _text(letter.signed_at) if signed and letter is not None else None,
            "mode": engagement.fee_unit,
            "fee": _hundredths(engagement.fee),
            "expected_days": _hundredths(engagement.expected_days),
            "role": engagement.role,
            "starts_on": _text(engagement.starts_on),
            "ends_on": _text(engagement.ends_on),
            "state": engagement.state,
        }
