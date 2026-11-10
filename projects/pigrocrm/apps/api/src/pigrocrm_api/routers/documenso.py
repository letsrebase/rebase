"""Documenso's webhook (REB-1019; the hub's `routers/documenso.py`, REB-387): a signature, a refusal
or a cancellation of an envelope, delivered to the root installation.

No cookie and no actor: Documenso authenticates with `X-Documenso-Secret`, the value typed into
the webhook's form, sent verbatim. It is compared in constant time with
`PIGROCRM_DOCUMENSO_WEBHOOK_SECRET`; an empty setting switches the route off (503) and a missing
header and an empty one are refused alike, since a webhook saved without a secret sends the header
empty. The check is a router dependency: a body that is not JSON at all is still a 422, since
FastAPI reads the body before it runs the dependencies, but a well-formed JSON body that fails the
model's own checks meets the secret first.

**Root only.** Documenso posts to the one URL of the installation, and the space is read from the
envelope's `externalId` (`documenso.routing.route_envelope`). A request that wears a space's own
prefix answers the same 404 as a route that does not exist, exactly like the engagements door.

**What is not ours is ignored.** The hub keeps signing in production until a separate cutover, and
its envelopes may sit on the same Documenso: a delivery whose `externalId` is not one this
installation wrote, that names a space that does not exist, that is a bare document id with no
leads space to resolve it in, or whose document in the routed space does not hold this envelope, is
acknowledged and nothing is touched. Every well-formed delivery is answered 200, handled or not, so
Documenso never retries an event we chose to ignore (it gives up after ten seconds and retries
three times within a fraction of a second, then never again).

The answer is fast on purpose: `SigningService.apply` locks and commits, and the slow part (the
sealed copy's download, the mails, the letters a framework agreement releases) runs after the
response in a session of its own (`SigningRecovery.finish`). A completion is not moved by `apply`:
the secret above travels in clear, so `finish` confirms it with Documenso itself, over the
installation's own API token, before the document counts as signed. A delivery that was missed
entirely is recovered by an admin's refresh or by the daily `pigrocrm automations` pass.
"""

import logging
import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.documenso.client import WebhookBody, outcome_from_webhook, parse_external_id
from pigrocrm.core.documenso.routing import SpaceRoute, route_envelope
from pigrocrm.core.documents.models import Document
from pigrocrm.core.storage.factory import space_storage_from_settings
from pigrocrm_api.deps import TenantsRegistryDep, _space_registry, space_session_factory
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.signing import SigningFactory, SigningFactoryDep
from pigrocrm_api.tenancy import tenant_slug

_log = logging.getLogger(__name__)

_HTTP_EXCEPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
    "required": ["detail"],
}


def _plain_response(description: str) -> dict[str, Any]:
    """The refusals of the secret and of the root door are plain `HTTPException`s, rendered by
    FastAPI's default handler as `{"detail": "..."}`, not as a problem document."""
    return {
        "description": description,
        "content": {"application/json": {"schema": _HTTP_EXCEPTION_SCHEMA}},
    }


def require_root(request: Request) -> None:
    """A request wearing a space's own prefix answers the 404 a route that does not exist gives:
    the webhook is the root installation's, and a space must never hint it exists under its name."""
    if tenant_slug(request) is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


def verify_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_documenso_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Reads `get_settings` directly, not `SettingsDep`: that one opens a session on the database
    of whatever space the URL names, and this door is the root's."""
    expected = settings.documenso_webhook_secret
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook_disabled")
    presented = x_documenso_secret or ""
    # Bytes, not str: `compare_digest` refuses a `str` with a non-ASCII character, and Starlette
    # decodes headers as latin-1.
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_secret")


router = APIRouter(
    prefix="/api/documenso",
    tags=["documenso"],
    responses={
        **PROBLEM_RESPONSES,
        401: _plain_response("The `X-Documenso-Secret` header is missing, empty or wrong."),
        503: _plain_response(
            "The installation has no `PIGROCRM_DOCUMENSO_WEBHOOK_SECRET`: the webhook is off."
        ),
    },
    dependencies=[Depends(require_root), Depends(verify_secret)],
)


class WebhookAck(BaseModel):
    ok: bool = True


def _open_space(
    settings: Settings, route: SpaceRoute, factory: SigningFactory
) -> tuple[Session, SigningService]:
    """A session on the routed space (the root for `slug` `None`), scoped to the system, with the
    space's effective settings and storage: what `get_request_settings` and `get_storage` give a
    request, for a caller that has none. The caller closes the session."""
    slug = route.slug
    session = space_session_factory(slug, settings)()
    try:
        # A session with no scope reads nothing: row-level security keys on it.
        bind_scope(session, Actor.system())
        effective = _space_registry(settings).effective_settings(slug, session, settings)
        storage = space_storage_from_settings(
            effective, slug, session_factory=lambda: space_session_factory(slug, settings)()
        )
        return session, factory(session, storage, effective, route.tenant_id)
    except Exception:
        session.close()
        raise


def _finish(
    settings: Settings, route: SpaceRoute, factory: SigningFactory, document_id: UUID
) -> None:
    """After the response, in a session of its own: it never raises, since nobody is left to read
    it."""
    try:
        session, signing = _open_space(settings, route, factory)
        try:
            signing.recovery.finish(document_id)
        finally:
            session.close()
    except Exception:
        _log.exception("finishing the signature of document %s failed", document_id)


@router.post("/webhook", response_model=WebhookAck)
def documenso_webhook(
    payload: WebhookBody,
    background: BackgroundTasks,
    registry: TenantsRegistryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    factory: SigningFactoryDep,
) -> WebhookAck:
    """Applies a signature, a refusal or a cancellation Documenso reports for an envelope this
    installation wrote. Answers 200 to every well-formed delivery, handled or not."""
    outcome = outcome_from_webhook(payload)
    if outcome is None:
        return WebhookAck()
    try:
        route = route_envelope(registry, settings, parse_external_id(payload.payload.external_id))
        if route is None:
            _log.info("webhook for envelope %s ignored: not ours", outcome.envelope_id)
            return WebhookAck()
        session, signing = _open_space(settings, route, factory)
        try:
            # The document the externalId names must hold this very envelope: the hub's own
            # envelopes, a forged externalId and a deleted space's leftovers all stop here.
            found = session.scalar(
                select(Document.id).where(Document.envelope_id == outcome.envelope_id)
            )
            if found is None or found != route.document_id:
                session.rollback()
                _log.info(
                    "webhook for envelope %s ignored: the routed space holds no such document",
                    outcome.envelope_id,
                )
                return WebhookAck()
            signed = signing.apply(outcome)
        except Exception:
            # `apply`'s own failure (a NotFound, a DB error) must not surface past the webhook:
            # the secret's owner reads no stack trace, and recovery is the admin's refresh or
            # the daily pass.
            session.rollback()
            raise
        finally:
            session.close()
    except Exception:
        _log.exception("applying the webhook for envelope %s failed", outcome.envelope_id)
        return WebhookAck()
    if signed is not None:
        background.add_task(_finish, settings, route, factory, signed)
    return WebhookAck()
