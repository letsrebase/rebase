"""Campaigns over HTTP (P-REB-41). `public` is the unsubscribe a mail's footer and its
`List-Unsubscribe` header point at; `router` (Task 17) is the admin's."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse

from rebase_api.deps import SessionDep, SettingsDep
from rebase_api.ratelimit import spend_one
from rebase_core.campaigns.optouts import TOKEN_MAX_LENGTH, OptoutService
from rebase_core.schemas import Ack

public = APIRouter(prefix="/api/hub/campagne", tags=["hub-campaigns-public"])

Token = Annotated[str, Query(min_length=1, max_length=TOKEN_MAX_LENGTH)]


@public.get("/disiscrizione")
def unsubscribe_page(t: Token, settings: SettingsDep) -> RedirectResponse:
    """A GET changes nothing: mail scanners fetch links. It sends the person to the page,
    where a button posts."""
    return RedirectResponse(
        f"{settings.hub_url.rstrip('/')}/disiscrizione?t={t}", status_code=status.HTTP_303_SEE_OTHER
    )


@public.post("/disiscrizione", response_model=Ack)
def unsubscribe(t: Token, request: Request, session: SessionDep) -> Ack:
    """The page's button and RFC 8058's one-click POST. The same answer for a token that
    matched and one that did not, so a guess learns nothing."""
    spend_one(request)
    OptoutService(session).unsubscribe(t)
    return Ack()
