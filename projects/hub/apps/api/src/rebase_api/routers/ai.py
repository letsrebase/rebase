"""«Modelli AI» (REB-833, plan Task 7): the admin's door to the provider keys, the two
tasks' provider/model/effort/caps, the price table, the month's spend and «Prova».

Admin only, every route. A key goes in through `PUT /providers/{provider}/key` and never
comes out: every answer here is the core's `ProviderRead`, whose most is the «configurata,
finisce con …abcd» hint. «Prova» runs the selected task's own provider, model and cap with
one fixed, non-personal prompt; the seam records the call as `purpose="prova"`, so a
probe spends the task's daily cap like a real card does."""

import time
from collections import Counter
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from rebase_api.deps import (
    AdminDep,
    CardBatchesDep,
    CardLlmDep,
    ProvaCallDep,
    SessionDep,
    SessionOpenerDep,
    SettingsDep,
)
from rebase_core.ai_config import MAX_AMOUNT_EUR, AiConfigService
from rebase_core.ai_schemas import (
    AiConfigRead,
    PriceRead,
    ProviderRead,
    RegenerationStarted,
    SourceSwitchRead,
    StaleCardRead,
    StaleCardsRead,
    TaskRead,
)
from rebase_core.card_batch import active_batch
from rebase_core.cards import CardWriter, regenerate_after_response
from rebase_core.errors import LlmUnavailable, NotFound, TeamBuilderOff
from rebase_core.llm import LlmMessage, LlmRequest
from rebase_core.models import AI_TASKS, AiUsage
from rebase_core.sources import default_source_service

router = APIRouter(prefix="/api/hub/admin/ai", tags=["hub-admin"])

PROVA_SYSTEM = "Rispondi in modo brevissimo."
PROVA_PROMPT = "Rispondi solo con la parola: ok."
PROVA_MAX_TOKENS = 64
# The most cards one start writes again, and the most the list shows: one start is a few
# minutes of calls, which the page does not wait for (they are written after the response).
REGENERATE_MAX = 50
STALE_ITEMS_SHOWN = 50


class KeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=512, repr=False)


class EnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class TaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=20)
    model: str = Field(min_length=1, max_length=100)
    effort: str | None = None
    max_output_tokens: int = Field(ge=1, le=200_000)
    daily_cap_eur: Decimal | None = Field(default=None, ge=0, le=MAX_AMOUNT_EUR)
    enabled: bool


class PriceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=20)
    model: str = Field(min_length=1, max_length=100)
    input_eur_per_mtok: Decimal = Field(ge=0, le=MAX_AMOUNT_EUR)
    output_eur_per_mtok: Decimal = Field(ge=0, le=MAX_AMOUNT_EUR)
    thinking_eur_per_mtok: Decimal | None = Field(default=None, ge=0, le=MAX_AMOUNT_EUR)
    cache_read_eur_per_mtok: Decimal | None = Field(default=None, ge=0, le=MAX_AMOUNT_EUR)


class RegenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=1, le=REGENERATE_MAX)


class ProvaRead(BaseModel):
    answer: str
    latency_ms: int
    cost_eur: str | None
    error: str | None


class _Probe(BaseModel):
    answer: str


def get_service(session: SessionDep, settings: SettingsDep) -> AiConfigService:
    return AiConfigService(session, settings)


ServiceDep = Annotated[AiConfigService, Depends(get_service)]


@router.get("", response_model=AiConfigRead)
def read_config(admin: AdminDep, service: ServiceDep) -> AiConfigRead:
    return service.read()


@router.put("/providers/{provider}/key", response_model=ProviderRead)
def save_key(admin: AdminDep, service: ServiceDep, provider: str, body: KeyBody) -> ProviderRead:
    """409 with the sentence while `REBASE_SECRETS_KEY` is missing: nothing is stored."""
    return service.save_key(admin.id, provider, body.key)


@router.put("/providers/{provider}/enabled", response_model=ProviderRead)
def set_enabled(
    admin: AdminDep, service: ServiceDep, provider: str, body: EnabledBody
) -> ProviderRead:
    return service.set_provider_enabled(admin.id, provider, body.enabled)


@router.put("/sources/{kind}/enabled", response_model=SourceSwitchRead)
def set_source_enabled(
    admin: AdminDep, service: ServiceDep, session: SessionDep, kind: str, body: EnabledBody
) -> SourceSwitchRead:
    """Turns one kind of source the card is written from (`github`, `links`) on or off.
    Off deletes the text already read at once (`SourceService.set_enabled`); both are off
    until the privacy notice covers reading what people linked."""
    default_source_service(session).set_enabled(admin.id, kind, body.enabled)
    return next(row for row in service.sources() if row.kind == kind)


@router.get("/cards/stale", response_model=StaleCardsRead)
def stale_cards(admin: AdminDep, session: SessionDep, settings: SettingsDep) -> StaleCardsRead:
    """«Schede da aggiornare»: the cards a newer prompt version or a change of the hub's
    facts, the position or a source has left behind, with what writing them again would
    cost. A card whose CV changed is not here: the hourly loop writes it by itself."""
    writer = CardWriter(session, None)
    waiting = writer.stale_cards()
    return StaleCardsRead(
        count=len(waiting),
        by_reason=dict(Counter(item.reason for item in waiting)),
        estimated_cost_eur=writer.estimate_cost(len(waiting)) if waiting else None,
        items=[
            StaleCardRead(
                freelancer_id=item.freelancer_id,
                nome=item.nome,
                reason=item.reason,
                generated_at=item.generated_at,
            )
            for item in waiting[:STALE_ITEMS_SHOWN]
        ],
        batch_threshold=settings.cards_batch_threshold,
        batch=active_batch(session),
    )


@router.post("/cards/regenerate-stale", response_model=RegenerationStarted)
def regenerate_stale(
    admin: AdminDep,
    session: SessionDep,
    llm: CardLlmDep,
    batches: CardBatchesDep,
    settings: SettingsDep,
    open_session: SessionOpenerDep,
    background: BackgroundTasks,
    body: RegenerateBody,
) -> RegenerationStarted:
    """An admin's start: the oldest `limit` stale cards are written again, `cards_workers` at
    a time, after the response, or, from `cards_batch_threshold` cards up with Anthropic,
    sent as one Message Batch at half the price that the `cards` service applies when it
    ends (`AdminAction` `cards_regenerate` is written when the start begins). A start while
    another is running, or a batch is not yet applied, answers `queued` 0. No model configured
    answers the team builder's own 503, as «Rigenera scheda» does."""
    if llm is None:
        raise TeamBuilderOff("Il team builder è spento.")
    writer = CardWriter(session, None)
    # A start while one is still inside its batch queues nothing: the cards it is writing
    # are still stale until it saves them, and a second batch would pay for them again.
    queued = 0 if writer.regeneration_running() else min(body.limit, len(writer.stale_cards()))
    if queued:
        background.add_task(
            regenerate_after_response,
            open_session,
            llm,
            admin.id,
            body.limit,
            workers=settings.cards_workers,
            batches=batches,
        )
    return RegenerationStarted(queued=queued)


@router.put("/tasks/{task}", response_model=TaskRead)
def update_task(admin: AdminDep, service: ServiceDep, task: str, body: TaskBody) -> TaskRead:
    return service.update_task(admin.id, task, **body.model_dump())


@router.put("/prices", response_model=PriceRead)
def upsert_price(admin: AdminDep, service: ServiceDep, body: PriceBody) -> PriceRead:
    return service.upsert_price(admin.id, **body.model_dump())


@router.post("/tasks/{task}/prova", response_model=ProvaRead)
def prova(
    admin: AdminDep,
    service: ServiceDep,
    session: SessionDep,
    make_call: ProvaCallDep,
    task: str,
) -> ProvaRead:
    """One call with the fixed prompt through the task's own provider, model and cap. A
    provider that fails answers 200 with `error` set to the seam's fixed sentence (never
    the provider's text, which could carry the key): the page shows it beside the button."""
    if task not in AI_TASKS:
        raise NotFound("ai_task", task)
    started = time.monotonic()
    try:
        response = make_call(task).complete(
            LlmRequest(
                system=PROVA_SYSTEM,
                messages=[LlmMessage(role="user", text=PROVA_PROMPT)],
                output_model=_Probe,
                max_tokens=PROVA_MAX_TOKENS,
            )
        )
    except LlmUnavailable as exc:
        return ProvaRead(
            answer="",
            latency_ms=round((time.monotonic() - started) * 1000),
            cost_eur=None,
            error=exc.message,
        )
    latency = round((time.monotonic() - started) * 1000)
    session.expire_all()
    usage = session.scalars(
        select(AiUsage)
        .where(AiUsage.task == task, AiUsage.purpose == "prova")
        .order_by(AiUsage.at.desc())
        .limit(1)
    ).first()
    cost = usage.cost_eur if usage is not None and usage.cost_eur is not None else None
    answer = response.parsed.answer if isinstance(response.parsed, _Probe) else response.text
    return ProvaRead(
        answer=answer or "",
        latency_ms=latency,
        cost_eur=None if cost is None else format(cost, "f"),
        error=None,
    )
