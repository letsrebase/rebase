"""The hub's one seam onto a language model (REB-508, REB-831): the callers -- the
anonymous card (spec § 5.1) and the team proposal (spec § 3.4) -- share `LlmCall` and
never see a provider's SDK. Like `EmailSender` in `mail.py`, a protocol, one real
implementation (`AiCall`, on Pydantic AI), a recording fake for the tests, and
`call_for_task` answering `None` when the task is not configured so a caller refuses
with its own sentence rather than guessing at one.

Which provider, model, effort and ceiling answer a task, and the provider's key, are read
from the database at call time (`AiConfigService`, «Modelli AI»), never from the
environment: a change an admin saves applies to the next call, and a call is refused
before any network when the task's daily euro cap is spent, its model has no price, or
its provider is off or has no key. Every call the provider answers writes its usage (a
refusal too, which is paid for); one that fails, an outage or a body that is not a
response, leaves none, as there are no tokens to read, and a usage row that cannot be
written is logged and does not lose the answer already paid for.
There is no fallback from one provider to another: an outage is a `LlmUnavailable`.

`AiCall` never raises past `complete` anything but a `LlmUnavailable` (or one of its two
subclasses), carrying the one sentence a page can show as it stands, because a
provider's own exceptions carry no Italian and no promise to keep meaning the same thing
after the next version. It is a `DomainError` (`errors.py`), so the API's
`domain_error_handler` answers a 502 and the MCP's `_call` answers the tool's own
sentence, both with no code of their own. Nothing here logs the request (not the CV text
a card is written from, not a project's description) nor an exception's text (a
provider's error can quote what it was sent): the log carries the exception's class and
the HTTP status, nothing else.

`build_model` is the only place that imports a provider.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai.direct import model_request
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    InstructionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.batch import Batch, BatchRequest, BatchResult
from pydantic_ai.output import OutputObjectDefinition
from pydantic_ai.settings import ModelSettings
from sqlalchemy.orm import Session

from rebase_core.ai_config import (
    CAP_REACHED,
    NO_KEY,
    NO_PRICE,
    PROVIDER_OFF,
    TASK_OFF,
    AiConfigService,
)
from rebase_core.config import Settings
from rebase_core.db import SessionOpener
from rebase_core.errors import (
    DomainError,
    LlmCapReached,
    LlmNotConfigured,
    LlmUnavailable,
)
from rebase_core.models import AiPrice, AiProvider, AiTask

logger = logging.getLogger(__name__)

# The one sentence a provider outage shows on a page (global-constraints.md).
UNAVAILABLE_SENTENCE = "Non riesco a proporre un team adesso: riprova tra poco."

# The server-side fallback opt-in, exactly as the spec's § 5 and the global constraints
# name it: never the older array form, never a client-side fallback list.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# The whole call has to end inside the 90 seconds the host vhost gives
# `/api/hub/team/proposals` (AGENTS.md, «The team builder»), or the visitor gets
# nginx's 504 while the proposal's slot stays held and a late answer writes a paid
# proposal nobody sees. The timeout is per attempt, so 40 seconds with one retry (and the
# SDK's backoff of half a second between them) ends near 81. (A `retry-after` the API
# sends replaces that half second, and nothing caps it: a long one on a 429 is the one
# way still past 90.)
_TIMEOUT_SECONDS = 40.0
_MAX_RETRIES = 1

# The API accepts only "global" or "us" for `inference_geo`; "eu" answers a 400
# ("must be one of ['global', 'us']", proven on the preview 2026-09-28). Keeping
# inference inside the European Union would need Vertex AI or Bedrock in an EU
# region instead of this SDK, which is a separate decision, not made here.
_INFERENCE_GEO = "global"

# What Anthropic does with a system block marked for caching when the request has one the
# caller flagged (`LlmRequest.system_cached`); every other provider reads only its own
# keys and ignores this one.
_CACHE_SYSTEM = {"anthropic_cache_instructions": True}


class LlmMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class LlmRequest(BaseModel):
    """What a caller asks, in no provider's shape. `system` is the rules. `system_cached`
    is text every call repeats (the team builder's catalogue), sent as a second system
    block and, where the provider has prompt caching, marked for it: both stay the same
    prefix for every visitor. `messages` alternate from `user`. `output_model` is the
    shape of the answer, the one place a limit (`max_length`, `ge`) is enforced: the
    provider is given the schema, and `AiCall` validates what comes back with the model
    itself. A docstring on it is for whoever maintains it and never goes to the model.
    The output ceiling is the task's (`ai_tasks.max_output_tokens`, set in «Modelli AI»);
    `max_tokens` is a ceiling of the caller's own, below it, for a call that needs less
    (the «Prova» probe)."""

    system: str
    system_cached: str | None = None
    messages: list[LlmMessage]
    output_model: type[BaseModel]
    max_tokens: int | None = None


class LlmResponse(BaseModel):
    text: str | None  # what the model wrote; `None` on a refusal
    parsed: BaseModel | None = None  # `text` validated as `output_model`; `None` if it is not
    stop_reason: str
    refusal_category: str | None
    model: str
    provider: str = ""
    # What was paid for as input: the uncached tokens and the cache writes (the
    # catalogue block, when its prefix is written again), not the cache reads, which
    # are `cache_read_tokens`.
    input_tokens: int
    # Without the thinking tokens, which are `thinking_tokens` where the provider says
    # how many; otherwise they are inside this.
    output_tokens: int
    cache_read_tokens: int
    thinking_tokens: int = 0


class LlmCall(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...


@dataclass(frozen=True)
class TaskOverride:
    """A call made as a task but answered by another provider or model, for an evaluation
    run (REB-834): the task's daily cap is still the one it consumes. `allow_disabled`
    lets an evaluation reach a provider that is switched off (`rebase cards-eval
    --allow-disabled`, REB-835): it sends CVs there, so it is explicit, and it still needs
    the key and the model's price. `max_output_tokens` replaces the task's output ceiling for
    this call: the evaluation's judge (REB-842) answers a verdict and a reason for eight
    fields, which a card task's ceiling is too small for. It is still a call of the task, and
    is recorded and capped as one."""

    provider: str
    model: str
    effort: str | None = None
    allow_disabled: bool = False
    max_output_tokens: int | None = None


ModelFactory = Callable[[str, str, str, str | None], tuple[Model, ModelSettings]]


def build_model(
    provider: str, model: str, api_key: str, effort: str | None
) -> tuple[Model, ModelSettings]:
    """The provider's model and the settings that go with it: the one place that imports
    `pydantic_ai.providers.*`. The key is handed to the provider, never read from the
    environment. `effort` is each provider's own knob: Anthropic's adaptive thinking at
    that effort, OpenAI's `reasoning_effort`, Google's `thinking_level`; `None` leaves the
    provider's default.

    Anthropic is called the way the hub always has been: the server-side fallback beta with
    `fallbacks="default"`, `inference_geo="global"`, adaptive thinking, and the request's
    schema passed through the SDK's `transform_schema` (Pydantic AI does that for a
    native output, which is what every call here asks for). `fallbacks` and
    `inference_geo` ride in `extra_body`, as Pydantic AI has no setting of its own for
    them."""
    common: dict[str, Any] = {"timeout": _TIMEOUT_SECONDS, "max_retries": _MAX_RETRIES}
    if provider == "anthropic":
        from pydantic_ai.providers.anthropic import AnthropicProvider

        from rebase_core.anthropic_model import ServerFallbackAnthropicModel

        anthropic_settings: dict[str, Any] = {
            **common,
            "anthropic_thinking": {"type": "adaptive"},
            "anthropic_betas": [FALLBACK_BETA],
            "extra_body": {"fallbacks": "default", "inference_geo": _INFERENCE_GEO},
        }
        if effort is not None:
            anthropic_settings["anthropic_effort"] = effort
        return (
            ServerFallbackAnthropicModel(model, provider=AnthropicProvider(api_key=api_key)),
            cast(ModelSettings, anthropic_settings),
        )
    if provider == "openai":
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        openai_settings: dict[str, Any] = dict(common)
        if effort is not None:
            openai_settings["openai_reasoning_effort"] = effort
        return (
            OpenAIResponsesModel(model, provider=OpenAIProvider(api_key=api_key)),
            cast(ModelSettings, openai_settings),
        )
    if provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        google_settings: dict[str, Any] = dict(common)
        if effort is not None:
            google_settings["google_thinking_config"] = {"thinking_level": effort.upper()}
        return (
            GoogleModel(model, provider=GoogleProvider(api_key=api_key)),
            cast(ModelSettings, google_settings),
        )
    raise LlmNotConfigured(f"Fornitore sconosciuto: {provider}")


def build_batch_model(
    provider: str, model: str, api_key: str, effort: str | None
) -> tuple[Model, ModelSettings]:
    """The Anthropic model and settings a Message Batch is encoded with (REB-853): what
    `build_model` sends, less the three things a batch refuses or cannot take per request.
    The server-side fallback (the beta and `fallbacks`: "Server-side fallback doesn't apply to
    batches", a batch item that carries it comes back errored) and the per-request `timeout`
    and `max_retries` (the provider runs the batched requests). The adaptive thinking, the
    effort and `inference_geo` are the same, and so is every other parameter, since the
    request is built by the same Pydantic AI code. Anthropic only: the other providers answer
    the way `build_model` does, one call at a time."""
    if provider != "anthropic":
        raise LlmNotConfigured(f"Il Batch API non c'è per il fornitore: {provider}")
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    settings: dict[str, Any] = {
        "anthropic_thinking": {"type": "adaptive"},
        "extra_body": {"inference_geo": _INFERENCE_GEO},
    }
    if effort is not None:
        settings["anthropic_effort"] = effort
    return (
        AnthropicModel(model, provider=AnthropicProvider(api_key=api_key)),
        cast(ModelSettings, settings),
    )


def _history(request: LlmRequest) -> list[ModelMessage]:
    history: list[ModelMessage] = []
    for message in request.messages:
        if message.role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=message.text)]))
        else:
            history.append(ModelResponse(parts=[TextPart(content=message.text)]))
    return history


def _parameters(request: LlmRequest) -> ModelRequestParameters:
    schema = {
        key: value
        for key, value in request.output_model.model_json_schema().items()
        if key != "description"
    }
    # An empty text block is not a block a provider takes (Anthropic answers a 400): no
    # rules, no system part.
    instructions = [InstructionPart(content=request.system)] if request.system.strip() else []
    if request.system_cached is not None:
        instructions.append(InstructionPart(content=request.system_cached))
    return ModelRequestParameters(
        output_mode="native",
        output_object=OutputObjectDefinition(
            json_schema=schema, name=request.output_model.__name__, strict=True
        ),
        instruction_parts=instructions,
    )


def _run[T](call: Coroutine[Any, Any, T]) -> T:
    """The coroutine to its end, on a loop of its own that closes after it, with the
    provider's HTTP client: the model is built per call, so nothing outlives the loop it ran
    on. A caller already inside a running loop (a sync MCP tool is run by one) cannot start
    a second in its own thread, so the call runs in a thread of its own and waits there."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(call)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, call).result()


def _stop_reason(response: ModelResponse) -> str:
    """Anthropic's own words where the provider has them, since the callers read
    `refusal` and `max_tokens`; the others' finish reasons are mapped onto the same two."""
    if response.finish_reason == "content_filter":
        return "refusal"
    if response.finish_reason == "length":
        return "max_tokens"
    raw = (response.provider_details or {}).get("finish_reason")
    return str(raw) if raw else "end_turn"


class AiCall:
    """The seam on Pydantic AI. Built once per request by `call_for_task`; every
    `complete` reads the task row, the price and the provider's key again, so a change an
    admin just saved applies at once, refuses before any network (`LlmCapReached`,
    `LlmNotConfigured`) and, after an answer, writes the call's usage.

    `purpose` is why the call runs (`run`, `prova`, `eval`: `AI_USAGE_PURPOSES`);
    `freelancer_id` is the person a card call is about. `model_factory` is the test's
    way to hand a stub model; production leaves it at `build_model`, called per call so
    the key is the one saved now."""

    def __init__(
        self,
        session_factory: SessionOpener,
        settings: Settings,
        task: str,
        *,
        override: TaskOverride | None = None,
        purpose: str = "run",
        freelancer_id: UUID | None = None,
        model_factory: ModelFactory = build_model,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._task = task
        self._override = override
        self._purpose = purpose
        self._freelancer_id = freelancer_id
        self._model_factory = model_factory

    def complete(self, request: LlmRequest) -> LlmResponse:
        provider, model, effort, ceiling, key = self._resolve()
        model_instance, model_settings = self._model_factory(provider, model, key, effort)
        max_tokens = ceiling if request.max_tokens is None else min(request.max_tokens, ceiling)
        settings = {**model_settings, "max_tokens": max_tokens}
        if request.system_cached is not None:
            settings.update(_CACHE_SYSTEM)
        try:
            answer = _run(
                model_request(
                    model_instance,
                    _history(request),
                    model_settings=cast(ModelSettings, settings),
                    model_request_parameters=_parameters(request),
                )
            )
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            status = error.status_code if isinstance(error, ModelHTTPError) else None
            logger.warning("%s %s: status %s", provider, type(error).__name__, status)
            raise LlmUnavailable(UNAVAILABLE_SENTENCE) from None
        return self._response(request, provider, model, answer)

    def _resolve(self) -> tuple[str, str, str | None, int, str]:
        """The provider, model, effort, output ceiling and key this call goes out with, or
        the refusal."""
        with self._session_factory() as session:
            service = AiConfigService(session, self._settings)
            row = session.get(AiTask, self._task)
            if row is None:
                raise LlmNotConfigured(TASK_OFF)
            if self._override is None:
                allowed, reason = service.can_call(self._task)
                provider, model, effort = row.provider, row.model, row.effort
            else:
                provider, model, effort = (
                    self._override.provider,
                    self._override.model,
                    self._override.effort,
                )
                allowed, reason = self._can_call_override(
                    service, row, provider, model, self._override.allow_disabled
                )
            if not allowed:
                message = reason or TASK_OFF
                if message == CAP_REACHED:
                    raise LlmCapReached(message)
                raise LlmNotConfigured(message)
            try:
                key = service.resolve_key(provider)
            except DomainError as error:  # no key text in it: `SecretsKey*` name the variable
                raise LlmNotConfigured(error.message) from None
            if key is None:
                raise LlmNotConfigured(NO_KEY)
            ceiling = (
                self._override.max_output_tokens
                if self._override is not None and self._override.max_output_tokens
                else row.max_output_tokens
            )
            return provider, model, effort, ceiling, key

    @staticmethod
    def _can_call_override(
        service: AiConfigService, row: AiTask, provider: str, model: str, allow_disabled: bool
    ) -> tuple[bool, str | None]:
        """The task's own cap, then the same checks for the provider and model the call
        will really go to."""
        session = service.session
        if row.daily_cap_eur is not None and service.spent_today(row.task) >= row.daily_cap_eur:
            return False, CAP_REACHED
        if session.get(AiPrice, (provider, model)) is None:
            return False, NO_PRICE
        account = session.get(AiProvider, provider)
        if account is None or not (account.enabled or allow_disabled):
            return False, PROVIDER_OFF
        if account.key_ciphertext is None:
            return False, NO_KEY
        return True, None

    def _response(
        self, request: LlmRequest, provider: str, configured: str, answer: ModelResponse
    ) -> LlmResponse:
        response = response_of(request, provider, answer)
        record_response(
            self._session_factory,
            self._settings,
            task=self._task,
            purpose=self._purpose,
            configured=configured,
            response=response,
            freelancer_id=self._freelancer_id,
        )
        return response


def response_of(request: LlmRequest, provider: str, answer: ModelResponse) -> LlmResponse:
    """What a provider's `ModelResponse` says, in the seam's own words: the stop reason, the
    text (none on a refusal), the body validated as the request's `output_model` (the shape is
    the caller's to judge; its text is never logged) and the tokens, thinking apart where the
    provider counts it. Nothing is recorded here."""
    stop_reason = _stop_reason(answer)
    details = answer.provider_details or {}
    usage = answer.usage
    thinking = usage.output_reasoning_tokens
    refused = stop_reason == "refusal"
    text = None if refused else (answer.text or None)
    parsed: BaseModel | None = None
    if text is not None:
        try:
            parsed = request.output_model.model_validate_json(text)
        except ValueError:
            parsed = None
    return LlmResponse(
        text=text,
        parsed=parsed,
        stop_reason=stop_reason,
        refusal_category=details.get("refusal_category") if refused else None,
        model=answer.model_name or "",
        provider=provider,
        input_tokens=usage.input_tokens - usage.cache_read_tokens,
        output_tokens=usage.output_tokens - thinking,
        cache_read_tokens=usage.cache_read_tokens,
        thinking_tokens=thinking,
    )


def record_response(
    session_factory: SessionOpener,
    settings: Settings,
    *,
    task: str,
    purpose: str,
    configured: str,
    response: LlmResponse,
    freelancer_id: UUID | None,
) -> None:
    """The answer's usage row. The model that answered may
    be a fallback or a dated alias with no price row: the call is then priced as the one
    asked for (`configured`), so it spends the cap. A lost row never loses the answer: it is
    paid for, so the failure is logged and the answer still handed over."""
    try:
        with session_factory() as session:
            AiConfigService(session, settings).record_usage(
                task=task,
                purpose=purpose,
                provider=response.provider,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                thinking_tokens=response.thinking_tokens,
                cache_read_tokens=response.cache_read_tokens,
                freelancer_id=freelancer_id,
                priced_as=configured,
            )
    except Exception as error:  # the answer is paid for: a lost row must not lose it
        logger.error("%s usage not recorded: %s", response.provider, type(error).__name__)


class RecordingCall:
    """Scripted responses, answered in order, the way `RecordingSender` (`mail.py`)
    keeps every mail it was asked to send: here it is every `LlmRequest`, in
    `.requests`, for the card writer's, the proposal engine's, the API's and the MCP's
    own tests to assert against, with no network and no provider import of their own."""

    def __init__(self, responses: list[LlmResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def call_for_task(
    session_factory: SessionOpener,
    settings: Settings,
    task: str,
    *,
    model_factory: ModelFactory = build_model,
) -> LlmCall | None:
    """The seam for `task` (`card` or `team_proposal`), or `None` when it cannot answer
    at all: the task off, its provider off or without a key. The callers then refuse with
    their own sentence. A cap reached or a missing price still gives a call, which refuses
    when it is made, with the reason «Modelli AI» shows."""
    with session_factory() as session:
        row = session.get(AiTask, task)
        if row is None or not row.enabled:
            return None
        provider = session.get(AiProvider, row.provider)
        if provider is None or not provider.enabled or provider.key_ciphertext is None:
            return None
    return AiCall(session_factory, settings, task, model_factory=model_factory)


# ---- the Message Batches API (REB-853) ----------------------------------------------------


@dataclass(frozen=True)
class BatchHandle:
    """What finds a submitted batch again from another process, after a restart: the
    provider's id, the model it was sent to, and the two things Pydantic AI checks a later
    call against (`group`, which carries hashes and no secret, and the creation time). Plain
    data, stored as JSON in `card_batches.handle`."""

    id: str
    provider: str
    model: str
    group: str
    created_at: datetime
    request_count: int

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "created_at": self.created_at.isoformat()}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BatchHandle":
        return cls(**{**data, "created_at": datetime.fromisoformat(data["created_at"])})

    def _batch(self, status: Literal["in_progress", "ended"]) -> Batch:
        return Batch(
            id=self.id,
            model_name=self.model,
            provider_name=self.provider,
            group=self.group,
            status=status,
            created_at=self.created_at,
            request_count=self.request_count,
        )


@dataclass(frozen=True)
class BatchOutcome:
    """How one request of an ended batch finished: `succeeded` with its `response`, or the
    provider's word for one that did not answer (`errored`, `expired`, `canceled`)."""

    status: Literal["succeeded", "errored", "expired", "canceled"]
    response: LlmResponse | None = None


class BatchCall(Protocol):
    """A provider's batch API as the card writer uses it, in no provider's shape: submit
    the requests under their ids (refused before any network when `estimate_eur`, what the
    batch is expected to cost, would take the task past its daily cap), ask whether the batch
    ended, collect each answer, and record an answer's usage at the batch price on the
    caller's session, left uncommitted for the caller to commit with what it marks as done.
    `provider` names who runs it."""

    provider: str

    def submit(
        self, requests: Mapping[str, LlmRequest], estimate_eur: Decimal | None = None
    ) -> BatchHandle: ...

    def ended(self, handle: BatchHandle) -> bool: ...

    def collect(
        self, handle: BatchHandle, output_model: type[BaseModel]
    ) -> dict[str, BatchOutcome]: ...

    def record(
        self, session: Session, handle: BatchHandle, response: LlmResponse, freelancer_id: UUID
    ) -> None: ...


def _unavailable(provider: str, error: Exception) -> LlmUnavailable:
    status = error.status_code if isinstance(error, ModelHTTPError) else None
    logger.warning("%s batch %s: status %s", provider, type(error).__name__, status)
    return LlmUnavailable(UNAVAILABLE_SENTENCE)


class AiBatch:
    """The seam's batch path, on Pydantic AI's own Message Batches primitives (`batch_encode`,
    `batch_create`, `batch_status`, `batch_results`, `batch_decode`): each request is encoded
    by the code that encodes a synchronous one, so it carries the same prompt, schema,
    model, effort and output ceiling, less the fallback and the per-request timeout and
    retries a batch refuses (`build_batch_model`). The provider, model, effort, ceiling and
    key are the task's, read again on every call.

    Only `submit` is refused by the task's cap, the key or the provider's switch, before any
    network, as a call is: an ended batch is already paid for, and its answers are collected
    whatever the cap says. `record` writes the usage at half the price row's. `model_factory`
    is the test's way to hand another model."""

    def __init__(
        self,
        session_factory: SessionOpener,
        settings: Settings,
        task: str,
        *,
        purpose: str = "run",
        model_factory: ModelFactory = build_batch_model,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._task = task
        self._purpose = purpose
        self._model_factory = model_factory
        self._call = AiCall(session_factory, settings, task, purpose=purpose)
        self.provider = "anthropic"

    def submit(
        self, requests: Mapping[str, LlmRequest], estimate_eur: Decimal | None = None
    ) -> BatchHandle:
        provider, model, effort, ceiling, key = self._call._resolve()
        self._require_room(estimate_eur)
        built, base = self._model_factory(provider, model, key, effort)
        encoded = [
            BatchRequest(
                custom_id=custom_id,
                messages=_history(request),
                model_settings=cast(ModelSettings, self._settings_of(base, request, ceiling)),
                model_request_parameters=_parameters(request),
            )
            for custom_id, request in requests.items()
        ]

        async def send() -> Batch:
            return await built.batch_create([await built.batch_encode(item) for item in encoded])

        try:
            batch = _run(send())
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            raise _unavailable(provider, error) from None
        return BatchHandle(
            id=batch.id,
            provider=provider,
            model=model,
            group=batch.group,
            created_at=batch.created_at,
            request_count=batch.request_count,
        )

    def ended(self, handle: BatchHandle) -> bool:
        built, _ = self._built(handle)
        try:
            return _run(built.batch_status(handle._batch("in_progress"))).status == "ended"
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            raise _unavailable(handle.provider, error) from None

    def collect(
        self, handle: BatchHandle, output_model: type[BaseModel]
    ) -> dict[str, BatchOutcome]:
        built, base = self._built(handle)
        shape = LlmRequest(system="", messages=[], output_model=output_model)
        ended = handle._batch("ended")
        try:
            results = _run(built.batch_results(ended))
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            raise _unavailable(handle.provider, error) from None
        outcomes: dict[str, BatchOutcome] = {}
        for result in results:
            outcomes[result.custom_id] = self._outcome(built, base, shape, handle, result)
        return outcomes

    def record(
        self, session: Session, handle: BatchHandle, response: LlmResponse, freelancer_id: UUID
    ) -> None:
        """The answer's usage row at the batch price, on `session` and not committed: the
        caller commits it with its own mark that it was recorded, so it is in the books
        once. A model with no price row (a dated alias) is priced as the one the batch was
        sent to. A failure is raised, not logged: nothing is marked recorded."""
        AiConfigService(session, self._settings).record_usage(
            task=self._task,
            purpose=self._purpose,
            provider=response.provider,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            thinking_tokens=response.thinking_tokens,
            cache_read_tokens=response.cache_read_tokens,
            freelancer_id=freelancer_id,
            priced_as=handle.model,
            batch=True,
            commit=False,
        )

    def _require_room(self, estimate_eur: Decimal | None) -> None:
        """What the batch is expected to cost fits in what is left of the task's daily cap
        (the call's own check only says the cap is not spent yet): the spend lands when the
        results are read, and one batch could otherwise overshoot it by its whole cost."""
        if estimate_eur is None:
            return
        with self._session_factory() as session:
            row = session.get(AiTask, self._task)
            if row is None or row.daily_cap_eur is None:
                return
            spent = AiConfigService(session, self._settings).spent_today(self._task)
            if spent + estimate_eur > row.daily_cap_eur:
                raise LlmCapReached(CAP_REACHED)

    def _outcome(
        self,
        built: Model,
        base: ModelSettings,
        shape: LlmRequest,
        handle: BatchHandle,
        result: BatchResult,
    ) -> BatchOutcome:
        if result.status != "succeeded":
            return BatchOutcome(result.status)
        decode = BatchRequest(
            custom_id=result.custom_id,
            messages=[],
            model_settings=base,
            model_request_parameters=_parameters(shape),
        )
        try:
            answer = built.batch_decode(result, decode)
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            logger.warning("%s batch result %s", handle.provider, type(error).__name__)
            return BatchOutcome("errored")
        return BatchOutcome("succeeded", response_of(shape, handle.provider, answer))

    def _built(self, handle: BatchHandle) -> tuple[Model, ModelSettings]:
        """The model and settings of an ended or running batch: the task's effort and the key
        saved now, with no cap or switch in the way."""
        with self._session_factory() as session:
            row = session.get(AiTask, self._task)
            if row is None:
                raise LlmNotConfigured(TASK_OFF)
            try:
                key = AiConfigService(session, self._settings).resolve_key(handle.provider)
            except DomainError as error:
                raise LlmNotConfigured(error.message) from None
            if key is None:
                raise LlmNotConfigured(NO_KEY)
            effort = row.effort
        return self._model_factory(handle.provider, handle.model, key, effort)

    @staticmethod
    def _settings_of(base: ModelSettings, request: LlmRequest, ceiling: int) -> dict[str, Any]:
        max_tokens = ceiling if request.max_tokens is None else min(request.max_tokens, ceiling)
        settings = {**base, "max_tokens": max_tokens}
        if request.system_cached is not None:
            settings.update(_CACHE_SYSTEM)
        return settings


class RecordingBatch:
    """A batch API that keeps what it was asked, as `RecordingCall` keeps the requests: every
    submitted request by id in `.submitted`, answers scripted by id in `.outcomes`, and the
    usage it was asked to record in `.recorded`. `ended_after` is how many `ended` checks say
    «not yet» first. A test's seam; no network."""

    provider = "anthropic"

    def __init__(
        self,
        outcomes: Mapping[str, BatchOutcome] | None = None,
        *,
        ended_after: int = 0,
        refuse: Exception | None = None,
    ) -> None:
        self.outcomes = dict(outcomes or {})
        self.submitted: dict[str, LlmRequest] = {}
        self.recorded: list[tuple[UUID, LlmResponse]] = []
        self.estimates: list[Decimal | None] = []
        self._waits = ended_after
        self._refuse = refuse
        self.submissions = 0

    def submit(
        self, requests: Mapping[str, LlmRequest], estimate_eur: Decimal | None = None
    ) -> BatchHandle:
        if self._refuse is not None:
            raise self._refuse
        self.estimates.append(estimate_eur)
        self.submissions += 1
        self.submitted.update(requests)
        return BatchHandle(
            id=f"msgbatch_{self.submissions:04d}",
            provider="anthropic",
            model="claude-test",
            group="{}",
            created_at=datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
            request_count=len(requests),
        )

    def ended(self, handle: BatchHandle) -> bool:
        if self._waits > 0:
            self._waits -= 1
            return False
        return True

    def collect(
        self, handle: BatchHandle, output_model: type[BaseModel]
    ) -> dict[str, BatchOutcome]:
        return dict(self.outcomes)

    def record(
        self, session: Session, handle: BatchHandle, response: LlmResponse, freelancer_id: UUID
    ) -> None:
        self.recorded.append((freelancer_id, response))


def batch_for_task(
    session_factory: SessionOpener,
    settings: Settings,
    task: str,
    *,
    model_factory: ModelFactory = build_batch_model,
) -> BatchCall | None:
    """The batch path for `task`, or `None` when it has none: the task off, or answered by a
    provider other than Anthropic (the only one whose batch API this speaks). A caller then
    writes the cards one call at a time, with the workers. A cap reached or a key missing
    still gives a batch, which refuses when it is submitted."""
    with session_factory() as session:
        row = session.get(AiTask, task)
        if row is None or not row.enabled or row.provider != "anthropic":
            return None
    return AiBatch(session_factory, settings, task, model_factory=model_factory)
