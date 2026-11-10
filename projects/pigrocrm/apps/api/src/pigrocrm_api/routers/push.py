"""Web push: the browsers a person receives their reminders on (spec 2026-10-26 §6.1,
REB-738). Every route is the caller's own, so every role has them, like the profile."""

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.reminders.push import push_public_key
from pigrocrm.core.reminders.push_subscriptions import (
    PushConfigRead,
    PushSubscriptionRead,
    PushSubscriptionService,
    PushSubscriptionWrite,
)
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/push",
    tags=["push"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


class Unsubscribe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(
        max_length=2048, description="The browser's push endpoint address to remove"
    )


@router.get("/config", response_model=PushConfigRead)
def read_config(settings: SettingsDep) -> PushConfigRead:
    """Whether this installation can send push notifications, and the key the browser
    subscribes with."""
    key = push_public_key(settings)
    return PushConfigRead(enabled=key is not None, public_key=key)


@router.get("/subscriptions", response_model=list[PushSubscriptionRead])
def my_subscriptions(session: SessionDep, actor: ActorDep) -> list[PushSubscriptionRead]:
    """The browsers on which you turned notifications on."""
    return PushSubscriptionService(session).mine(actor)


@router.post("/subscribe", response_model=PushSubscriptionRead)
def subscribe(
    data: PushSubscriptionWrite, request: Request, session: SessionDep, actor: ActorDep
) -> PushSubscriptionRead:
    """Turns notifications on for this browser. If the same browser belonged to another
    person, it is yours from now on."""
    return PushSubscriptionService(session).subscribe(
        actor, data, request.headers.get("user-agent")
    )


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(data: Unsubscribe, session: SessionDep, actor: ActorDep) -> None:
    """Turns notifications off for this browser. Nothing to turn off? That is fine
    too."""
    PushSubscriptionService(session).unsubscribe(actor, data.endpoint)
