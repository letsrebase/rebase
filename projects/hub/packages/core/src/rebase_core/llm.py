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
its provider is off or has no key. Every call that reaches a provider writes its usage.
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
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
from pydantic_ai.output import OutputObjectDefinition
from pydantic_ai.settings import ModelSettings

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
    run (REB-834): the task's daily cap is still the one it consumes."""

    provider: str
    model: str
    effort: str | None = None


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
        return self._response(request, provider, answer)

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
                allowed, reason = self._can_call_override(service, row, provider, model)
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
            return provider, model, effort, row.max_output_tokens, key

    @staticmethod
    def _can_call_override(
        service: AiConfigService, row: AiTask, provider: str, model: str
    ) -> tuple[bool, str | None]:
        """The task's own cap, then the same checks for the provider and model the call
        will really go to."""
        session = service.session
        if row.daily_cap_eur is not None and service.spent_today(row.task) >= row.daily_cap_eur:
            return False, CAP_REACHED
        if session.get(AiPrice, (provider, model)) is None:
            return False, NO_PRICE
        account = session.get(AiProvider, provider)
        if account is None or not account.enabled:
            return False, PROVIDER_OFF
        if account.key_ciphertext is None:
            return False, NO_KEY
        return True, None

    def _response(self, request: LlmRequest, provider: str, answer: ModelResponse) -> LlmResponse:
        stop_reason = _stop_reason(answer)
        details = answer.provider_details or {}
        usage = answer.usage
        thinking = usage.output_reasoning_tokens
        input_tokens = usage.input_tokens - usage.cache_read_tokens
        output_tokens = usage.output_tokens - thinking
        with self._session_factory() as session:
            AiConfigService(session, self._settings).record_usage(
                task=self._task,
                purpose=self._purpose,
                provider=provider,
                model=answer.model_name or "",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_tokens=thinking,
                cache_read_tokens=usage.cache_read_tokens,
                freelancer_id=self._freelancer_id,
            )
        refused = stop_reason == "refusal"
        text = None if refused else (answer.text or None)
        parsed: BaseModel | None = None
        if text is not None:
            try:
                parsed = request.output_model.model_validate_json(text)
            except ValueError:  # the shape is the caller's to judge; its text never logged
                parsed = None
        return LlmResponse(
            text=text,
            parsed=parsed,
            stop_reason=stop_reason,
            refusal_category=details.get("refusal_category") if refused else None,
            model=answer.model_name or "",
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            thinking_tokens=thinking,
        )


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
