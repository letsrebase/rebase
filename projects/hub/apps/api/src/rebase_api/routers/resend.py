"""Resend's webhook (P-REB-41, spec § 6.1). No cookie: Resend signs with Svix, verified
over the raw body against REBASE_RESEND_WEBHOOK_SECRET. A tagged event whose row is not
found yet answers 503, the one answer that makes Resend retry (immediately, 5 s, 5 min,
30 min, 2 h, 5 h, 10 h, 10 h): the tick may not have committed it. Everything else that
verified answers 200. The `detail` of a refusal is an English code, not a sentence: nobody
reads it but the sender's log."""

import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from rebase_api.deps import SessionDep, SettingsDep
from rebase_core.campaigns.webhook import apply_event, verify_signature
from rebase_core.schema_common import Ack

router = APIRouter(prefix="/api/hub/webhooks", tags=["hub-resend"])


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/resend", response_model=Ack)
def resend_webhook(
    settings: SettingsDep,
    session: SessionDep,
    body: Annotated[bytes, Depends(raw_body)],
    svix_id: Annotated[str | None, Header()] = None,
    svix_timestamp: Annotated[str | None, Header()] = None,
    svix_signature: Annotated[str | None, Header()] = None,
) -> Ack:
    if not settings.resend_webhook_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook_disabled")
    if not verify_signature(
        settings.resend_webhook_secret,
        svix_id or "",
        svix_timestamp or "",
        svix_signature or "",
        body,
        time.time(),
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_signature")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_body") from None
    if not isinstance(event, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_body")
    if apply_event(session, event) == "retry":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "retry")
    return Ack()
