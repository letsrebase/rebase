"""The signing service as the API builds it (REB-1019): one dependency for the routes of a space,
and the factory both it and the root webhook build from.

The factory is the seam. Production builds `SigningService` from the settings, which gives it a
Documenso client only when `PIGROCRM_DOCUMENSO_URL` and `PIGROCRM_DOCUMENSO_API_TOKEN` are both set
and a mail sender only with a Resend key; a test overrides `get_signing_factory` with one that hands
in a fake Documenso and a recording sender, so no test ever calls a real one.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings
from pigrocrm.core.documenso.routing import tenant_id_for
from pigrocrm.core.storage import DocumentStorage
from pigrocrm_api.deps import SessionDep, SettingsDep, StorageDep, TenantsRegistryDep
from pigrocrm_api.tenancy import tenant_slug

SigningFactory = Callable[[Session, DocumentStorage, Settings, UUID | None], SigningService]


def build_signing(
    session: Session, storage: DocumentStorage, settings: Settings, tenant_id: UUID | None
) -> SigningService:
    """`tenant_id` is the space's registry id, `None` for the root installation: what the
    envelope's `externalId` names so the root webhook finds the space again."""
    return SigningService(session, storage, settings, tenant_id=tenant_id)


def get_signing_factory() -> SigningFactory:
    return build_signing


SigningFactoryDep = Annotated[SigningFactory, Depends(get_signing_factory)]


def get_signing(
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    registry: TenantsRegistryDep,
    factory: SigningFactoryDep,
) -> SigningService:
    """The signing service of the request's own space. The registry is asked for the space's id
    only under a space's prefix: the root installation has none."""
    tenant_id = tenant_id_for(registry, settings, tenant_slug(request))
    return factory(session, storage, settings, tenant_id)


SigningDep = Annotated[SigningService, Depends(get_signing)]
