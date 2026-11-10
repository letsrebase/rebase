"""The freelancer's own engagement, read from their own space (brief
`2026-11-10-engagement-pages-brief.md`, «The freelancer's own view»; collaborators design § 9
excludes self-service beyond recording days, so this only reads).

The opposite direction to the live report, which rebase's space reads from the freelancer's: here
a freelancer's space asks rebase's. It goes through the registry, never by a name the caller
chooses. The registry row the door wrote for the contract (`rebase_engagements`, tenant and
contract) names the hub match, and the match names the engagement in the space
`PIGROCRM_LEADS_SPACE` holds (`hub_match_id`, or the engagement's own id, as the report reads
it). A space can therefore read the engagement of a contract the door wrote *in that space* and
no other: no id, slug or freelancer is taken from the request beyond the contract it is already
looking at.

What comes back is `collaborators.own_view.own_view`'s closed list of fields. Imported by its
full path, like `engagements.service` (see the package docstring).
"""

import logging
from uuid import UUID

from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.models import REPORTING_STATES, Engagement
from pigrocrm.core.collaborators.own_view import own_view
from pigrocrm.core.collaborators.schemas import OwnEngagementRead
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.service import EngagementService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.tenants.models import Tenant

logger = logging.getLogger(__name__)

ENTITY = "engagement"


class OwnEngagementService:
    """On the registry's engine, like `EngagementService`, whose space session it borrows."""

    def __init__(self, registry_engine: Engine, settings: Settings) -> None:
        self.registry_engine = registry_engine
        self.settings = settings
        self.door = EngagementService(registry_engine, settings)

    def for_contract(self, space_slug: str | None, contract_id: UUID) -> OwnEngagementRead:
        """The engagement the door wrote `contract_id` of space `space_slug` for, as its
        freelancer reads it. `NotFound` when there is none: a contract that did not come from an
        engagement, a space that is not a freelancer's, an engagement not yet signed (the door
        writes the contract when it turns active) or one that was cancelled, or an installation
        with no leads space."""
        leads = self.settings.leads_space.strip()
        if space_slug is None or not leads:
            raise NotFound(ENTITY, str(contract_id))
        with session_factory(self.registry_engine)() as registry:
            tenant = registry.scalar(select(Tenant).where(Tenant.slug == space_slug))
            row = (
                registry.scalar(
                    select(RebaseEngagement).where(
                        RebaseEngagement.tenant_id == tenant.id,
                        RebaseEngagement.contract_id == contract_id,
                    )
                )
                if tenant is not None
                else None
            )
            leads_tenant = registry.scalar(select(Tenant).where(Tenant.slug == leads))
            if row is None or leads_tenant is None:
                raise NotFound(ENTITY, str(contract_id))
            match_id = row.match_id
        with self.door._space_session(leads_tenant) as space:
            return self._read(space, match_id, contract_id)

    def _read(self, space: Session, match_id: UUID, contract_id: UUID) -> OwnEngagementRead:
        engagement = space.scalars(
            select(Engagement).where(
                or_(Engagement.hub_match_id == match_id, Engagement.id == match_id),
                Engagement.state.in_(REPORTING_STATES),
            )
        ).first()
        if engagement is None:
            raise NotFound(ENTITY, str(contract_id))
        return own_view(space, engagement, today_local(self.settings))
