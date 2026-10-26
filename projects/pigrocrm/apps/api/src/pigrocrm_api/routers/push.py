"""Web push: the browsers a person receives their reminders on (spec 2026-10-26 §6.1,
REB-738). Every route is the caller's own, so every role has them, like the profile."""

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.reminders.push import push_public_key
from pigrocrm.core.reminders.push_subscriptions import (
    PushConfigRead,
    PushSubscriptionRead,
    PushSubscriptionService,
    PushSubscriptionWrite,
)
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/push", tags=["push"], responses=PROBLEM_RESPONSES)


class Unsubscribe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(max_length=2048, description="L'indirizzo del browser da togliere")


@router.get("/config", response_model=PushConfigRead)
def read_config(settings: SettingsDep, actor: ActorDep) -> PushConfigRead:
    """Se questa installazione può mandare notifiche push, e la chiave con cui il browser
    si iscrive."""
    key = push_public_key(settings)
    return PushConfigRead(enabled=key is not None, public_key=key)


@router.get("/subscriptions", response_model=list[PushSubscriptionRead])
def my_subscriptions(session: SessionDep, actor: ActorDep) -> list[PushSubscriptionRead]:
    """I browser su cui hai attivato le notifiche."""
    return PushSubscriptionService(session).mine(actor)


@router.post("/subscribe", response_model=PushSubscriptionRead)
def subscribe(
    data: PushSubscriptionWrite, request: Request, session: SessionDep, actor: ActorDep
) -> PushSubscriptionRead:
    """Attiva le notifiche su questo browser. Se lo stesso browser era di un'altra
    persona, da adesso è tuo."""
    return PushSubscriptionService(session).subscribe(
        actor, data, request.headers.get("user-agent")
    )


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(data: Unsubscribe, session: SessionDep, actor: ActorDep) -> None:
    """Toglie le notifiche da questo browser. Non c'è niente da togliere? Va bene lo
    stesso."""
    PushSubscriptionService(session).unsubscribe(actor, data.endpoint)
