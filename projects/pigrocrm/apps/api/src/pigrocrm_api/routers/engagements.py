"""rebase's engagements door (spec 2026-09-25 § 2.3, § 2.4): the two routes the hub
calls under `PIGROCRM_ENGAGEMENTS_TOKEN`, on the root installation, exactly like
`GET /api/tenants/` under `PIGROCRM_REGISTRY_TOKEN` -- one bearer the whole caller holds,
checked by `require_service_token` (`service_token.py`) before either route touches
`EngagementService`.

No `ActorDep`: the caller is the hub itself, not a person or an agent of a space.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.engagements.schemas import EngagementRead, EngagementReport, EngagementUpsert
from pigrocrm.core.engagements.service import EngagementService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.mail import EmailSender
from pigrocrm_api.deps import SettingsDep, TenantsRegistryDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.service_token import require_service_token
from pigrocrm_api.sessions import SenderDep

router = APIRouter(
    prefix="/api/rebase/engagements", tags=["engagements"], responses=PROBLEM_RESPONSES
)


def _service(
    registry: Session, settings: Settings, sender: EmailSender | None
) -> EngagementService:
    """`EngagementService` opens its own engine per space and keeps none between calls
    (its own docstring), so a request-scoped `Session` lends it only the engine behind
    it -- never a session already inside this request's own transaction."""
    bind = registry.get_bind()
    # `TenantsRegistryDep` binds a session to an Engine, never a bare Connection.
    assert isinstance(bind, Engine)
    return EngagementService(bind, settings, sender)


def _as_public_url_problem(exc: ValidationFailed) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message)


@router.put("/{match_id}", response_model=EngagementRead)
def upsert_engagement(
    match_id: UUID,
    data: EngagementUpsert,
    response: Response,
    registry: TenantsRegistryDep,
    settings: SettingsDep,
    sender: SenderDep,
    authorization: Annotated[str | None, Header()] = None,
) -> EngagementRead:
    """Crea o ritrova lo spazio del freelancer, il cliente «rebase» e il deal della
    lettera per questo match dell'hub, sotto il bearer di `PIGROCRM_ENGAGEMENTS_TOKEN`
    che solo l'hub possiede."""
    require_service_token(settings.engagements_token, authorization)
    try:
        answer = _service(registry, settings, sender).ensure(match_id, data)
    except ValidationFailed as exc:
        if exc.details.get("field") != "public_url":
            raise
        raise _as_public_url_problem(exc) from exc
    response.status_code = status.HTTP_201_CREATED if answer.creato else status.HTTP_200_OK
    return answer


@router.get("/{match_id}/report", response_model=EngagementReport)
def engagement_report(
    match_id: UUID,
    registry: TenantsRegistryDep,
    settings: SettingsDep,
    sender: SenderDep,
    authorization: Annotated[str | None, Header()] = None,
    da: date | None = None,
    a: date | None = None,
) -> EngagementReport:
    """Legge le ore del deal di questo match nel periodo dato, con le fatture su cui
    siedono, sotto lo stesso bearer di `PIGROCRM_ENGAGEMENTS_TOKEN` che solo l'hub
    possiede."""
    require_service_token(settings.engagements_token, authorization)
    try:
        return _service(registry, settings, sender).report(match_id, da=da, a=a)
    except ValidationFailed as exc:
        if exc.details.get("field") != "public_url":
            raise
        raise _as_public_url_problem(exc) from exc
