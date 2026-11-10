"""Which space an envelope belongs to (REB-1019, collaborators design § 2, «Routing the webhook»).

Documenso posts to one URL on the root, so the webhook reads the space from the envelope's
`externalId`. A new envelope carries `<tenant id>:<document id>` (`client.external_id`): the
registry id of the space, or `root` for the root installation. An envelope the hub sent before the
cutover, or one imported with its documents, carries the bare document id, which resolves in the
only space that held hub documents, the one `PIGROCRM_LEADS_SPACE` names. Anything else is not this
installation's envelope (another system sharing the Documenso, a title typed by hand) and routes
nowhere: the webhook acknowledges it and does nothing.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.documenso.client import ROOT_TENANT, ExternalId
from pigrocrm.core.errors import NotFound
from pigrocrm.core.tenants.service import TenantService


@dataclass(frozen=True)
class SpaceRoute:
    """The installation an envelope's document lives in: `slug` is the space's, `None` the root
    installation. `tenant_id` is the registry id the document's envelope names, `None` for the
    root; `document_id` is the document the `externalId` named."""

    slug: str | None
    tenant_id: UUID | None
    document_id: UUID
    bare: bool


def tenant_id_for(registry: Session, settings: Settings, slug: str | None) -> UUID | None:
    """The registry id of the space `slug` names, `None` for the root installation (no slug, or the
    root's own name). The id an envelope's `externalId` is written with."""
    if slug is None or (settings.root_slug and slug == settings.root_slug):
        return None
    return TenantService(registry, settings).get(slug).id


def route_envelope(
    registry: Session, settings: Settings, external: ExternalId | None
) -> SpaceRoute | None:
    """The space an envelope's `externalId` names, or `None` when it names none this installation
    holds: not an id this code wrote, a space that no longer exists, or a bare document id with no
    leads space to resolve it in."""
    if external is None:
        return None
    if external.tenant is None:
        leads = settings.leads_space.strip()
        if not leads:
            return None
        if settings.root_slug and leads == settings.root_slug:
            return SpaceRoute(None, None, external.document_id, bare=True)
        try:
            tenant = TenantService(registry, settings).get(leads)
        except NotFound:
            return None
        return SpaceRoute(tenant.slug, tenant.id, external.document_id, bare=True)
    if external.tenant == ROOT_TENANT:
        return SpaceRoute(None, None, external.document_id, bare=False)
    tenant_row = TenantService(registry, settings).get_by_id(UUID(external.tenant))
    if tenant_row is None:
        return None
    return SpaceRoute(tenant_row.slug, tenant_row.id, external.document_id, bare=False)
