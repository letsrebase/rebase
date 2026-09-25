"""Documenso's webhook (REB-387, phase 3): a signature, a refusal or a cancellation.

No cookie: Documenso authenticates with `X-Documenso-Secret`, the value typed into the
webhook's form, sent verbatim (probe § 5). It is compared in constant time with
`REBASE_DOCUMENSO_WEBHOOK_SECRET`, and a missing header and an empty one are refused
alike, since a webhook saved without a secret sends the header empty (probe § 11.4). The
check is a dependency, but FastAPI reads and decodes the body before running it along
with the route's other parameters, so a malformed body with no secret at all still
answers 422, not 401.

The answer is fast on purpose. Documenso gives up on a delivery after ten seconds and
retries at once, three times within about 160 ms, then never again (probe § 5): the
route only locks the freelancer's row, its match and the document, in that order, and
commits (`SigningService.apply`), and the slow part (the sealed copy's download, the two
mails, the letters a framework agreement releases) runs after the response, in a session
of its own (`SigningService.finish`). A completion is not moved by `apply`: the secret
above travels in clear, so a `DOCUMENT_COMPLETED` only tells `apply` which document to
hand to `finish`, which confirms it with Documenso itself, over the hub's own API token,
before it counts as `firmato` (REB-431) -- a forged event then needs the token too.
Every well-formed delivery is answered 200, handled or not, so Documenso never retries
an event the hub chose to ignore. A delivery the hub missed entirely is recovered by an
admin's «Aggiorna stato», or, unattended, by `rebase contracts-sweep`.
"""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from rebase_api.deps import (
    SessionDep,
    SessionOpener,
    SessionOpenerDep,
    SettingsDep,
    SigningDep,
    SigningFactory,
)
from rebase_core.documenso import WebhookBody, outcome_from_webhook
from rebase_core.schemas import Ack

router = APIRouter(prefix="/api/hub", tags=["hub-documenso"])

_log = logging.getLogger(__name__)


def verify_secret(
    settings: SettingsDep,
    x_documenso_secret: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.documenso_webhook_secret
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "La firma elettronica non è attiva su questo ambiente.",
        )
    presented = x_documenso_secret or ""
    # Bytes, not str: `compare_digest` refuses a `str` with a non-ASCII character, and
    # Starlette decodes headers as latin-1 (the same care as `/members/lookup`).
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "segreto non valido")


def _finish(open_session: SessionOpener, build: SigningFactory, document_id: UUID) -> None:
    """After the response: it never raises, since nobody is left to read it."""
    try:
        with open_session() as session:
            build(session).finish(document_id)
    except Exception:
        _log.exception("finishing the signature of document %s failed", document_id)


@router.post("/documenso/webhook", response_model=Ack, dependencies=[Depends(verify_secret)])
def documenso_webhook(
    payload: WebhookBody,
    background: BackgroundTasks,
    session: SessionDep,
    signing: SigningDep,
    open_session: SessionOpenerDep,
) -> Ack:
    outcome = outcome_from_webhook(payload)
    if outcome is None:
        return Ack()
    try:
        signed = signing(session).apply(outcome)
    except Exception:
        # `apply`'s own failure (a NotFound, a DB error) must not surface past the
        # webhook: the secret's owner reads no stack trace, and recovery is «Aggiorna
        # stato» (REB-391).
        session.rollback()
        _log.exception("applying the webhook for envelope %s failed", payload.payload.envelope_id)
        return Ack()
    if signed is not None:
        background.add_task(_finish, open_session, signing, signed)
    return Ack()
