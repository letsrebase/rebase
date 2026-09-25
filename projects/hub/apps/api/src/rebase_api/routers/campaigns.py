"""Campaigns over HTTP (P-REB-41). `public` is the unsubscribe a mail's footer and its
`List-Unsubscribe` header point at; `router` (Task 17) is the admin's."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from rebase_api.deps import AdminDep, CampaignSenderDep, SessionDep, SettingsDep
from rebase_api.ratelimit import spend_one
from rebase_core.campaigns.optouts import TOKEN_MAX_LENGTH, OptoutService
from rebase_core.campaigns.schemas import (
    AudiencePreview,
    CampaignDetail,
    CampaignDraft,
    CampaignList,
    CampaignPatch,
    CampaignRead,
    NeverWriteRequest,
    ScheduleRequest,
    TemplateRead,
)
from rebase_core.campaigns.service import CampaignService
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


router = APIRouter(prefix="/api/hub/campaigns", tags=["hub-admin"])
NO_SENDER = "L'invio di mail non è configurato su questo ambiente."


@router.get("", response_model=CampaignList)
def list_campaigns(_: AdminDep, session: SessionDep, settings: SettingsDep) -> CampaignList:
    return CampaignService(session, settings).list_all()


@router.get("/templates", response_model=list[TemplateRead])
def templates(_: AdminDep, session: SessionDep, settings: SettingsDep) -> list[TemplateRead]:
    return CampaignService(session, settings).templates()


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def create(
    admin: AdminDep, session: SessionDep, settings: SettingsDep, data: CampaignDraft
) -> CampaignRead:
    return CampaignService(session, settings).create(admin.id, data)


@router.post("/never-write", response_model=Ack)
def never_write(_: AdminDep, session: SessionDep, data: NeverWriteRequest) -> Ack:
    OptoutService(session).never_write(str(data.email))
    return Ack()


@router.get("/{campaign_id}", response_model=CampaignDetail)
def detail(
    _: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID
) -> CampaignDetail:
    return CampaignService(session, settings).detail(campaign_id)


@router.patch("/{campaign_id}", response_model=CampaignRead)
def update(
    _: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID, data: CampaignPatch
) -> CampaignRead:
    return CampaignService(session, settings).update(campaign_id, data)


@router.get("/{campaign_id}/audience", response_model=AudiencePreview)
def audience(
    _: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID
) -> AudiencePreview:
    return CampaignService(session, settings).audience(campaign_id)


@router.post("/{campaign_id}/test", response_model=CampaignRead)
def send_test(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    sender: CampaignSenderDep,
    campaign_id: UUID,
) -> CampaignRead:
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_SENDER)
    return CampaignService(session, settings).send_test(campaign_id, admin, sender)


@router.post("/{campaign_id}/schedule", response_model=CampaignRead)
def schedule(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    sender: CampaignSenderDep,
    campaign_id: UUID,
    data: ScheduleRequest,
) -> CampaignRead:
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_SENDER)
    return CampaignService(session, settings).schedule(campaign_id, data)


@router.post("/{campaign_id}/draft", response_model=CampaignRead)
def back_to_draft(
    _: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID
) -> CampaignRead:
    return CampaignService(session, settings).back_to_draft(campaign_id)


@router.post("/{campaign_id}/cancel", response_model=CampaignRead)
def cancel(
    _: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID
) -> CampaignRead:
    return CampaignService(session, settings).cancel(campaign_id)
