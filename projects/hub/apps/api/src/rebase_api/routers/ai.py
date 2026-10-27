"""«Modelli AI» (REB-833, plan Task 7): the admin's door to the provider keys, the two
tasks' provider/model/effort/caps, the price table, the month's spend and «Prova».

Admin only, every route. A key goes in through `PUT /providers/{provider}/key` and never
comes out: every answer here is the core's `ProviderRead`, whose most is the «configurata,
finisce con …abcd» hint. «Prova» runs the selected task's own provider, model and cap with
one fixed, non-personal prompt; the seam records the call as `purpose="prova"`, so a
probe spends the task's daily cap like a real card does."""

import time
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from rebase_api.deps import AdminDep, ProvaCallDep, SessionDep, SettingsDep
from rebase_core.ai_config import MAX_AMOUNT_EUR, AiConfigService
from rebase_core.ai_schemas import AiConfigRead, PriceRead, ProviderRead, TaskRead
from rebase_core.errors import LlmUnavailable, NotFound
from rebase_core.llm import LlmMessage, LlmRequest
from rebase_core.models import AI_TASKS, AiUsage

router = APIRouter(prefix="/api/hub/admin/ai", tags=["hub-admin"])

PROVA_SYSTEM = "Rispondi in modo brevissimo."
PROVA_PROMPT = "Rispondi solo con la parola: ok."
PROVA_MAX_TOKENS = 64


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
