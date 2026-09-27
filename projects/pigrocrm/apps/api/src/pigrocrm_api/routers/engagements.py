"""rebase's engagements door (spec 2026-09-25 § 2.3, § 2.4): the two routes the hub
calls under `PIGROCRM_ENGAGEMENTS_TOKEN`, on the root installation, exactly like
`GET /api/tenants/` under `PIGROCRM_REGISTRY_TOKEN` -- one bearer the whole caller holds,
checked by `require_service_token` (`service_token.py`) before either route touches
`EngagementService`.

No `ActorDep`: the caller is the hub itself, not a person or an agent of a space.
"""

import logging
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.engagements.schemas import EngagementRead, EngagementReport, EngagementUpsert
from pigrocrm.core.engagements.service import (
    EngagementBusy,
    EngagementService,
    SpaceUnreachable,
)
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.mail import EmailSender
from pigrocrm_api.deps import SettingsDep, TenantsRegistryDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.service_token import require_service_token
from pigrocrm_api.sessions import SenderDep
from pigrocrm_api.tenancy import tenant_slug

logger = logging.getLogger(__name__)

SPAZIO_NON_RAGGIUNGIBILE = (
    "Lo spazio del freelancer non è raggiungibile in questo momento: riprova più tardi."
)


def _require_root_door(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Spec § 2.1: the door is root-only, exactly like `GET /api/tenants/`. A request
    wearing a space's own prefix answers the same 404 an unconfigured installation
    gives -- never a hint the door exists under some space's own name, where
    `space_base_settings` folds the slug into `PIGROCRM_PUBLIC_URL` and would double
    it into the links this service builds (`space_url`/`deal_url`).

    Reads `get_settings` directly, not `SettingsDep`: that dependency opens a session
    scoped to whatever slug the URL wears (`deps.get_session` -> `_tenant_session_factory`),
    and for a slug that happens to name a real tenant it would succeed rather than
    refuse -- handing the route that tenant's own, slug-doubled settings instead of
    the root's. Checked as a router-level dependency, not inside the route body, so it
    runs before FastAPI parses the request's body or path: a caller with no bearer
    gets 401 whatever the body says, not a 422 that never needed the token at all."""
    if tenant_slug(request) is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    require_service_token(settings.engagements_token, authorization)


router = APIRouter(
    prefix="/api/rebase/engagements",
    tags=["engagements"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(_require_root_door)],
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


def _run[T](match_id: UUID, action: Callable[[], T]) -> T:
    """Runs one call into `EngagementService` for `match_id`, turning every way the
    door answers "not now" into a 503 in one place, which the hub retries (a 409 or a
    422 it files as refused, for good): `ValidationFailed` on `public_url` is a missing
    setting, the installation's fault, not the caller's (spec § 2.1); `EngagementBusy`
    is another call still holding the freelancer's address; `SpaceUnreachable` is the
    space's database missing, unreachable or not migrated yet; any other
    `SQLAlchemyError` is the registry's own connection lost.

    The last two are logged with the match's id and, for a space, the tenant's id, so
    a space a restart left half-provisioned can be found from the log; never its slug
    (the freelancer's name) nor the address. The error by type only, never its message
    -- which can carry a statement's own bound parameters (`EngagementService.LOCK`'s
    address) into the traceback, the same reason `EngagementService._unlock` logs its
    own failure by type alone."""
    try:
        return action()
    except ValidationFailed as exc:
        if exc.details.get("field") != "public_url":
            raise
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from exc
    except EngagementBusy as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except SpaceUnreachable as exc:
        logger.warning(
            "engagements: match %s, space %s not reachable (%s)",
            match_id,
            exc.tenant_id,
            type(exc.__cause__).__name__,
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, SPAZIO_NON_RAGGIUNGIBILE) from exc
    except SQLAlchemyError as exc:
        logger.warning(
            "engagements: match %s, the door's own call failed (%s)",
            match_id,
            type(exc).__name__,
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, SPAZIO_NON_RAGGIUNGIBILE) from exc


_HTTP_EXCEPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
    "required": ["detail"],
}


def _plain_response(description: str) -> dict[str, Any]:
    """The 401/404 `require_service_token` raises and the 503 `_run` raises are both
    plain `HTTPException`s, rendered by FastAPI's own default handler as
    `application/json` `{"detail": "..."}` -- never `domain_error_handler`'s
    `application/problem+json` (`errors.py`'s `_problem_response` documents that shape,
    not this one, and `PROBLEM_RESPONSES` above never claims a 503 at all)."""
    return {
        "description": description,
        "content": {"application/json": {"schema": _HTTP_EXCEPTION_SCHEMA}},
    }


_UNAVAILABLE_RESPONSE = _plain_response(
    "PIGROCRM_PUBLIC_URL non è configurato, lo spazio del freelancer non è raggiungibile "
    "in questo momento, oppure un'altra chiamata sta preparando lo spazio dello stesso "
    "indirizzo: si riprova più tardi."
)


@router.put(
    "/{match_id}",
    response_model=EngagementRead,
    responses={
        201: {
            "model": EngagementRead,
            "description": (
                "Lo spazio, il cliente «rebase» e il deal della lettera sono stati "
                "creati ora, per la prima volta con questo match."
            ),
        },
        503: _UNAVAILABLE_RESPONSE,
    },
)
def upsert_engagement(
    match_id: UUID,
    data: EngagementUpsert,
    response: Response,
    registry: TenantsRegistryDep,
    settings: SettingsDep,
    sender: SenderDep,
) -> EngagementRead:
    """Crea o ritrova lo spazio del freelancer, il cliente «rebase» e il deal della
    lettera per questo match dell'hub, sotto il bearer di `PIGROCRM_ENGAGEMENTS_TOKEN`
    che solo l'hub possiede."""
    answer = _run(match_id, lambda: _service(registry, settings, sender).ensure(match_id, data))
    response.status_code = status.HTTP_201_CREATED if answer.creato else status.HTTP_200_OK
    return answer


@router.get(
    "/{match_id}/report",
    response_model=EngagementReport,
    responses={503: _UNAVAILABLE_RESPONSE},
)
def engagement_report(
    match_id: UUID,
    registry: TenantsRegistryDep,
    settings: SettingsDep,
    sender: SenderDep,
    da: date | None = None,
    a: date | None = None,
) -> EngagementReport:
    """Legge le ore del deal di questo match nel periodo dato, con le fatture su cui
    siedono, sotto lo stesso bearer di `PIGROCRM_ENGAGEMENTS_TOKEN` che solo l'hub
    possiede."""
    return _run(match_id, lambda: _service(registry, settings, sender).report(match_id, da=da, a=a))
