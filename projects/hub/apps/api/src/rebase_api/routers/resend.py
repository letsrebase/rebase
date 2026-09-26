"""Resend's webhook (P-REB-41, spec § 6.1). No cookie: Resend signs with Svix, verified
over the raw body against REBASE_RESEND_WEBHOOK_SECRET. A tagged event whose row is not
found yet answers 503, the one answer that makes Resend retry (immediately, 5 s, 5 min,
30 min, 2 h, 5 h, 10 h, 10 h): the tick may not have committed it. Everything else that
verified answers 200."""

import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from rebase_api.deps import SessionDep, SettingsDep
from rebase_core.campaigns.webhook import apply_event, verify_signature
from rebase_core.schemas import Ack

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
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Il webhook di Resend non è attivo su questo ambiente.",
        )
    if not verify_signature(
        settings.resend_webhook_secret,
        svix_id or "",
        svix_timestamp or "",
        svix_signature or "",
        body,
        time.time(),
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "firma non valida")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "corpo non valido") from None
    if not isinstance(event, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "corpo non valido")
    if apply_event(session, event) == "da_riprovare":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "riprova")
    return Ack()
