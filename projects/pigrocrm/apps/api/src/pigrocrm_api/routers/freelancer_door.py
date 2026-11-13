"""The hub's reads of a freelancer through the root door (REB-1107, collaborators design § 5):
availability, the member's contracts and their signed PDF, the fiscal identifiers the anonymous CV
redacts, and the merge check, each by the hub's own freelancer id.

Same door as `engagements.py`: root-only (a space's own prefix answers the 404 a route that does
not exist gives) and behind `PIGROCRM_ENGAGEMENTS_TOKEN`, checked by the router-level
`_require_root_door` before a path or a query is parsed. No `ActorDep`: the caller is the hub
itself. Every route is a `GET` and so idempotent, which is what lets the hub retry them. They read
the one space `PIGROCRM_LEADS_SPACE` names, never one the caller chooses.

A freelancer this installation holds no organization for is an empty answer, never a 404. The one
refusal is a document that is not theirs, a 404 like a document that does not exist. A leads space
that is not configured, or whose database cannot be reached, is a 503 the hub retries.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.door import HubDoorService
from pigrocrm.core.collaborators.door_schemas import (
    DoorAvailability,
    DoorContracts,
    DoorFiscalIdentifiers,
    DoorMergeCheck,
)
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.errors import NotFound
from pigrocrm.core.storage import DocumentStorage
from pigrocrm.core.storage.factory import space_storage_from_settings
from pigrocrm_api.deps import TenantsRegistryDep, _space_registry, space_session_factory
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.routers.engagements import _plain_response, _require_root_door

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "Lo spazio dei lead non è configurato (PIGROCRM_LEADS_SPACE)."
NOT_REACHABLE = "Lo spazio dei lead non è raggiungibile in questo momento: riprova più tardi."

router = APIRouter(
    prefix="/api/rebase/freelancers",
    tags=["engagements"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(_require_root_door)],
)

_UNAVAILABLE = _plain_response(
    "The leads space is not configured (PIGROCRM_LEADS_SPACE) or cannot be reached right now: "
    "retry later."
)
SettingsOfTheRoot = Annotated[Settings, Depends(get_settings)]


@contextmanager
def _door(
    settings: Settings, registry: Session, *, with_storage: bool = False
) -> Iterator[HubDoorService]:
    """The door's service on a session of the leads space, scoped as `Actor.rebase()` (the door is
    unscoped by design, spec 2026-10-03 § 4), with the space's effective settings. Every way the
    space cannot answer is a 503: the installation's fault or a database not there, never the
    caller's."""
    slug = settings.leads_space.strip()
    if not slug:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED)
    # The leads space may be the root installation itself.
    target = None if settings.root_slug and slug == settings.root_slug else slug
    session: Session | None = None
    try:
        session = space_session_factory(target, settings)()
        bind_scope(session, Actor.rebase())
        effective = _space_registry(settings).effective_settings(target, session, settings)
        storage: DocumentStorage | None = (
            space_storage_from_settings(
                effective, target, session_factory=lambda: space_session_factory(target, settings)()
            )
            if with_storage
            else None
        )
        yield HubDoorService(session, effective, settings, storage=storage, registry=registry)
    except NotFound as exc:
        # Raised by the registry for a slug nobody registered, before the service runs; a
        # `NotFound` the service itself raises (a document that is not theirs) is the caller's.
        if session is not None:
            raise
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED) from exc
    except SQLAlchemyError as exc:
        # The error by type only: its message can carry a statement's own bound parameters.
        logger.warning("freelancer door: the leads space failed (%s)", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_REACHABLE) from exc
    finally:
        if session is not None:
            session.close()


@router.get(
    "/{hub_freelancer_id}/availability",
    response_model=DoorAvailability,
    responses={503: _UNAVAILABLE},
)
def freelancer_availability(
    hub_freelancer_id: UUID,
    settings: SettingsOfTheRoot,
    registry: TenantsRegistryDep,
    date_from: Annotated[
        date | None,
        Query(alias="from", description="The week of this day is the first; default this week"),
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="Default twelve weeks on, at most 104")
    ] = None,
) -> DoorAvailability:
    """The days a week the freelancer is committed to, by ISO week, from their live engagements;
    `unknown` («sconosciuta») for a week touched by an engagement with no `days_per_week`, never
    `free`. A freelancer the CRM holds nothing about has every week free."""
    with _door(settings, registry) as door:
        return door.availability(hub_freelancer_id, date_from, date_to)


@router.get(
    "/{hub_freelancer_id}/contracts",
    response_model=DoorContracts,
    responses={503: _UNAVAILABLE},
)
def freelancer_contracts(
    hub_freelancer_id: UUID, settings: SettingsOfTheRoot, registry: TenantsRegistryDep
) -> DoorContracts:
    """The documents that reached the freelancer, newest first: their state, the signing link
    while one waits for the signature, the renewal and last day for a notice of a framework
    agreement. Empty for a freelancer the CRM holds nothing about."""
    with _door(settings, registry) as door:
        return door.contracts(hub_freelancer_id)


@router.get(
    "/{hub_freelancer_id}/contracts/{document_id}/pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}},
        503: _UNAVAILABLE,
    },
)
def freelancer_signed_pdf(
    hub_freelancer_id: UUID,
    document_id: UUID,
    settings: SettingsOfTheRoot,
    registry: TenantsRegistryDep,
) -> Response:
    """The signed copy of one of the freelancer's own documents. Someone else's document, one with
    no signed copy and one that does not exist are the same 404."""
    with _door(settings, registry, with_storage=True) as door:
        content, filename = door.signed_pdf(hub_freelancer_id, document_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{hub_freelancer_id}/fiscal-identifiers",
    response_model=DoorFiscalIdentifiers,
    responses={503: _UNAVAILABLE},
)
def freelancer_fiscal_identifiers(
    hub_freelancer_id: UUID, settings: SettingsOfTheRoot, registry: TenantsRegistryDep
) -> DoorFiscalIdentifiers:
    """Tax code, VAT number and certified email of the freelancer's organization, for the
    anonymous CV's redaction list. Null for each the CRM does not hold."""
    with _door(settings, registry) as door:
        return door.fiscal_identifiers(hub_freelancer_id)


@router.get(
    "/{hub_freelancer_id}/merge-check",
    response_model=DoorMergeCheck,
    responses={503: _UNAVAILABLE},
)
def freelancer_merge_check(
    hub_freelancer_id: UUID,
    settings: SettingsOfTheRoot,
    registry: TenantsRegistryDep,
    with_: Annotated[UUID, Query(alias="with", description="The other card, by hub id")],
) -> DoorMergeCheck:
    """What stops two hub cards becoming one: a live framework agreement of the same type on
    both, an engagement with the same client on both, or two different tax codes."""
    with _door(settings, registry) as door:
        return door.merge_check(hub_freelancer_id, with_)
