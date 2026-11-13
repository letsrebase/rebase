"""«Crea match» hands the engagement over to the CRM (REB-1105, collaborators design § 5,
«Creating an engagement»).

The engagement is the CRM's: its form, its letter, its signature. What the hub still does when an
admin pairs a freelancer card with a company request is three things, in this order:

1. The CRM finds or writes the freelancer's organization from the hub's card through the root door
   (`FreelancerDoor.ensure_organization`), by the hub freelancer id and never by name.
2. The hub mints the engagement's id and keeps a `hub_engagements` row for it in the state
   `handoff`, with the company's daily budget snapshot: the number the referral reward needs and
   the one that never reaches a letter. Nothing reads a `handoff` row; the CRM's first notice for
   the id upserts it. Opening the same pair again reuses the row and its id, so a second click
   never makes a second engagement.
3. The admin is sent to the CRM's engagement form in the leads space, filled from the link: the
   deal, its customer, the freelancer's organization and the minted id as `hub_match_id`.

A request that is not in the CRM yet has no deal to open the form on: that is an answer
(`HandoffRead.state == "no_deal"`), not an error, and the page offers «Riprova» before asking
again. The CRM's space address is the one the request's deal link already carries
(`{origin}/{slug}/app/deal/{id}`), so no setting names the leads space twice.
"""

from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.crm_link import contact_point_payload
from rebase_core.errors import NotFound
from rebase_core.match_words import LINKED, REJECTED
from rebase_core.models import Company, Freelancer, FreelancerFiscal, HubEngagement, User
from rebase_core.pigro_door import FreelancerDoor, speaks_https

NOT_LINKED = (
    "Questa richiesta non è ancora nel CRM: senza il suo deal non c'è un incarico da aprire. "
    "Riprova a collegarla."
)
DEAL_ADDRESS_UNREADABLE = (
    "Non riesco a ricavare lo spazio del CRM dal deal di questa richiesta: ricollegala e riprova."
)
HANDED_OFF = "handoff"


class HandoffFailed(Exception):
    """The CRM did not take the freelancer: `sentence` is what the admin reads (the CRM's own for
    a refusal, ours for a call that did not get an answer) and `rejected` tells a refusal that
    asking again will not change from one that may pass."""

    def __init__(self, sentence: str, *, rejected: bool) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.rejected = rejected


class HandoffRead(BaseModel):
    """`ready`: open `url`, the CRM's form, under `engagement_id`. `no_deal`: the request has no
    deal in the CRM yet, `message` says so and nothing was written."""

    state: Literal["ready", "no_deal"]
    url: str | None
    engagement_id: UUID | None
    message: str | None


def space_app_url(deal_url: str, deal_id: UUID) -> str | None:
    """The CRM space's `.../<slug>/app` from the deal's link, `None` for a link that is not
    `https://<host>/<slug>/app/deal/<deal id>` (plain HTTP only to this machine)."""
    suffix = f"/deal/{deal_id}"
    if not speaks_https(deal_url) or not deal_url.endswith(suffix):
        return None
    base = deal_url.removesuffix(suffix)
    return base if base.endswith("/app") else None


def form_url(
    app_url: str,
    *,
    engagement_id: UUID,
    deal_id: UUID,
    freelancer_organization_id: UUID,
    client_id: UUID | None,
) -> str:
    """`{app_url}/engagements?hub_match_id=…&freelancer_id=…&deal_id=…[&client_id=…]`: the CRM's
    page of the space's engagements, which opens its form on these (`search.ts` there)."""
    query: dict[str, str] = {
        "hub_match_id": str(engagement_id),
        "freelancer_id": str(freelancer_organization_id),
        "deal_id": str(deal_id),
    }
    if client_id is not None:
        query["client_id"] = str(client_id)
    return f"{app_url}/engagements?{urlencode(query)}"


class EngagementHandoff:
    def __init__(self, session: Session, door: FreelancerDoor) -> None:
        self.session = session
        self.door = door

    def open(self, freelancer_id: UUID, company_id: UUID) -> HandoffRead:
        freelancer = self.session.scalars(
            select(Freelancer).where(
                Freelancer.id == freelancer_id, Freelancer.deleted_at.is_(None)
            )
        ).first()
        if freelancer is None:
            raise NotFound("freelance", freelancer_id)
        company = self.session.scalars(
            select(Company).where(Company.id == company_id, Company.deleted_at.is_(None))
        ).first()
        if company is None:
            raise NotFound("richiesta", company_id)
        if company.crm_status != LINKED or company.crm_deal_id is None:
            return HandoffRead(state="no_deal", url=None, engagement_id=None, message=NOT_LINKED)
        app_url = space_app_url(company.crm_deal_url or "", company.crm_deal_id)
        if app_url is None:
            return HandoffRead(
                state="no_deal", url=None, engagement_id=None, message=DEAL_ADDRESS_UNREADABLE
            )

        organization = self._organization(freelancer)
        engagement_id = self._handoff_row(freelancer.id, company)
        return HandoffRead(
            state="ready",
            message=None,
            engagement_id=engagement_id,
            url=form_url(
                app_url,
                engagement_id=engagement_id,
                deal_id=company.crm_deal_id,
                freelancer_organization_id=organization,
                client_id=company.crm_customer_id,
            ),
        )

    def _organization(self, freelancer: Freelancer) -> UUID:
        """The freelancer's organization in the CRM, from the card as the hub holds it now.
        The session's read transaction is let go before the call: no lock is held while the CRM
        answers."""
        user = self.session.scalars(select(User).where(User.id == freelancer.user_id)).one()
        fiscal = self.session.scalars(
            select(FreelancerFiscal).where(FreelancerFiscal.freelancer_id == freelancer.id)
        ).first()
        card: dict[str, Any] = {
            "legal_name": f"{user.first_name} {user.last_name}".strip(),
            "email": user.email,
            "contact_points": contact_point_payload(user.contact_points),
        }
        if fiscal is not None:
            card["tax_code"] = fiscal.tax_code
            if fiscal.vat_number:
                card["vat_number"] = fiscal.vat_number
        freelancer_id = freelancer.id
        self.session.rollback()
        outcome = self.door.ensure_organization(freelancer_id, card)
        if outcome.answer is None:
            raise HandoffFailed(
                outcome.error or "Il CRM non ha risposto.", rejected=outcome.status == REJECTED
            )
        return outcome.answer.organization_id

    def _handoff_row(self, freelancer_id: UUID, company: Company) -> UUID:
        """The id this pair was handed over under: the one an earlier `handoff` row of the
        pair already has (the budget snapshot refreshed), else a new one. A pair whose
        engagement the CRM already noticed (any other state) gets a new id: that is another
        engagement."""
        row = self.session.scalars(
            select(HubEngagement)
            .where(
                HubEngagement.freelancer_id == freelancer_id,
                HubEngagement.company_id == company.id,
                HubEngagement.state == HANDED_OFF,
            )
            .order_by(HubEngagement.created_at.desc())
            .limit(1)
            .with_for_update()
        ).first()
        if row is None:
            row = HubEngagement(
                id=uuid4(),
                freelancer_id=freelancer_id,
                company_id=company.id,
                state=HANDED_OFF,
                sequence=0,
            )
            self.session.add(row)
        row.company_daily_budget = company.daily_budget
        engagement_id = row.id
        self.session.commit()
        return engagement_id
