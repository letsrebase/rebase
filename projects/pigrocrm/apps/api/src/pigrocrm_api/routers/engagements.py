"""rebase's leads door (spec 2026-10-30 § 2): `PUT /api/rebase/leads/{request_id}`, which the
hub calls under `PIGROCRM_ENGAGEMENTS_TOKEN`, on the root installation, exactly like
`GET /api/tenants/` under `PIGROCRM_REGISTRY_TOKEN` -- one bearer the whole caller holds,
checked by `require_service_token` (`service_token.py`) before the route touches
`LeadService`. The hub no longer creates engagements through a door route (REB-1108): the
CRM's own collaborators flow does, and the hub receives its notices.

`_require_root_door` and `_plain_response` are shared with the freelancer door
(`freelancer_door.py`).

No `ActorDep`: the caller is the hub itself, not a person or an agent of a space.
"""

import logging
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.engagements.leads import (
    LeadBusy,
    LeadService,
    LeadsSpaceNotConfigured,
)
from pigrocrm.core.engagements.schemas import LeadRead, LeadUpsert
from pigrocrm.core.engagements.service import SpaceUnreachable
from pigrocrm.core.errors import ValidationFailed
from pigrocrm_api.deps import SettingsDep, TenantsRegistryDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.service_token import require_service_token
from pigrocrm_api.tenancy import tenant_slug

logger = logging.getLogger(__name__)

SPACE_LEAD_NOT_REACHABLE = (
    "Lo spazio dei lead non è raggiungibile in questo momento: riprova più tardi."
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
    it into the links this service builds (`deal_url`).

    Reads `get_settings` directly, not `SettingsDep`: that dependency opens a session
    scoped to whatever slug the URL wears (`deps.get_session` -> `space_session_factory`),
    and for a slug that happens to name a real tenant it would succeed rather than
    refuse -- handing the route that tenant's own, slug-doubled settings instead of
    the root's. Checked as a router-level dependency, not inside the route body, so it
    runs before FastAPI parses the request's body or path: a caller with no bearer
    gets 401 whatever the body says, not a 422 that never needed the token at all."""
    if tenant_slug(request) is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    require_service_token(settings.engagements_token, authorization)


def _registry_engine(registry: Session) -> Engine:
    """The engine behind a request-scoped registry session. `TenantsRegistryDep` binds a
    session to an Engine, never a bare Connection."""
    bind = registry.get_bind()
    if not isinstance(bind, Engine):
        raise TypeError(f"the registry session is bound to {type(bind).__name__}, not an Engine")
    return bind


def _run[T](request_id: UUID, action: Callable[[], T]) -> T:
    """Runs one call into `LeadService` for `request_id`, turning every way the door
    answers "not now" into a 503 in one place, which the hub retries (a 409 or a 422 it
    files as refused, for good): `ValidationFailed` on `public_url` is a missing setting,
    the installation's fault, not the caller's; `LeadBusy` is another request from the same
    person still in progress; `LeadsSpaceNotConfigured` is the leads space unset;
    `SpaceUnreachable` is the space's database missing, unreachable or not migrated yet;
    any other `SQLAlchemyError` is the registry's own connection lost.

    The last two are logged with the request's id and, for a space, the tenant's id, never
    its slug nor the address. The error by type only, never its message -- which can carry
    a statement's own bound parameters into the traceback, the same reason
    `EngagementService._unlock` logs its own failure by type alone."""
    try:
        return action()
    except ValidationFailed as exc:
        if exc.details.get("field") != "public_url":
            raise
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from exc
    except (LeadBusy, LeadsSpaceNotConfigured) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except SpaceUnreachable as exc:
        logger.warning(
            "engagements: request %s, space %s not reachable (%s)",
            request_id,
            exc.tenant_id,
            type(exc.__cause__).__name__,
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, SPACE_LEAD_NOT_REACHABLE) from exc
    except SQLAlchemyError as exc:
        logger.warning(
            "engagements: request %s, the door's own call failed (%s)",
            request_id,
            type(exc).__name__,
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, SPACE_LEAD_NOT_REACHABLE) from exc


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


leads_router = APIRouter(
    prefix="/api/rebase/leads",
    tags=["engagements"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(_require_root_door)],
)

_LEADS_UNAVAILABLE_RESPONSE = _plain_response(
    "The leads space is not configured (PIGROCRM_LEADS_SPACE), PIGROCRM_PUBLIC_URL is not "
    "configured, the space cannot be reached right now, or another request from the same "
    "person is in progress: retry later."
)


@leads_router.put(
    "/{request_id}",
    response_model=LeadRead,
    responses={
        201: {
            "model": LeadRead,
            "description": "The request's deal was created now.",
        },
        503: _LEADS_UNAVAILABLE_RESPONSE,
    },
)
def file_lead(
    request_id: UUID,
    data: LeadUpsert,
    response: Response,
    registry: TenantsRegistryDep,
    settings: SettingsDep,
) -> LeadRead:
    """Archives in the rebase space the request of a hub company: client,
    contact and a new deal for each request, under the bearer of
    `PIGROCRM_ENGAGEMENTS_TOKEN` that only the hub holds. A second call with the
    same `request_id` finds the deal and writes nothing; with `deal_id` the request is
    linked to an existing deal and nothing else is created. The space is the one in
    `PIGROCRM_LEADS_SPACE`, never one chosen by the caller."""
    bind = _registry_engine(registry)
    answer = _run(request_id, lambda: LeadService(bind, settings).file(request_id, data))
    response.status_code = status.HTTP_201_CREATED if answer.created else status.HTTP_200_OK
    return answer
