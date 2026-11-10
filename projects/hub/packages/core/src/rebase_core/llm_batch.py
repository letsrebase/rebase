"""The Message Batches path of the seam (REB-853): the same request, encoded by Pydantic
AI's own batch primitives, sent as one batch at half the price, answered within a day.

`llm.py` is the seam a caller sees (`LlmCall`, `LlmRequest`, `LlmResponse`, the three
refusals); this is its batch half, split off so neither file outgrows its job (REB-892).
`BatchCall` is what the card writer holds, `AiBatch` the real one, `RecordingBatch` the
tests' and `batch_for_task` the door. Nothing here logs a request or an exception's text,
as `llm.py` promises.

A submitted batch holds its expected cost against the task's daily cap (REB-889): `submit`
places the hold (`AiConfigService.reserve`, under the task's lock, so a call and a batch
see each other), the handle carries its id, and the first answer `record`ed releases it in
the caller's own transaction, where the real, batch-priced rows take its place. A batch
that ends with no answer to record releases it in `collect`; a crash leaves it to its TTL.
"""

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UserError
from pydantic_ai.models import Model
from pydantic_ai.models.batch import Batch, BatchRequest, BatchResult
from pydantic_ai.settings import ModelSettings
from sqlalchemy.orm import Session

from rebase_core.ai_config import NO_KEY, TASK_OFF, AiConfigService
from rebase_core.config import Settings
from rebase_core.db import SessionOpener
from rebase_core.errors import DomainError, LlmNotConfigured, LlmUnavailable
from rebase_core.llm import (
    CACHE_SYSTEM,
    INFERENCE_GEO,
    AiCall,
    LlmRequest,
    LlmResponse,
    ModelFactory,
    _history,
    _parameters,
    _run,
    response_of,
)
from rebase_core.llm_sentences import unavailable_sentence
from rebase_core.models import AiTask

logger = logging.getLogger(__name__)


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
        "extra_body": {"inference_geo": INFERENCE_GEO},
    }
    if effort is not None:
        settings["anthropic_effort"] = effort
    return (
        AnthropicModel(model, provider=AnthropicProvider(api_key=api_key)),
        cast(ModelSettings, settings),
    )


# ---- the Message Batches API (REB-853) ----------------------------------------------------


@dataclass(frozen=True)
class BatchHandle:
    """What finds a submitted batch again from another process, after a restart: the
    provider's id, the model it was sent to, and the two things Pydantic AI checks a later
    call against (`group`, which carries hashes and no secret, and the creation time), and
    the spend hold that stands for the batch until its answers are recorded. Plain data,
    stored as JSON in `card_batches.handle`."""

    id: str
    provider: str
    model: str
    group: str
    created_at: datetime
    request_count: int
    hold_id: UUID | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["hold_id"] = None if self.hold_id is None else str(self.hold_id)
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BatchHandle":
        hold = data.get("hold_id")  # absent from a handle stored before spend holds
        return cls(
            **{
                **data,
                "created_at": datetime.fromisoformat(data["created_at"]),
                "hold_id": None if hold is None else UUID(hold),
            }
        )

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
        provider, model, effort, ceiling, key = self._call.resolve()
        hold_id = self._reserve(provider, model, estimate_eur)
        try:
            batch = self._send(requests, provider, model, effort, ceiling, key)
        except BaseException:
            if hold_id is not None:
                self._release(hold_id, provider)
            raise
        return BatchHandle(
            id=batch.id,
            provider=provider,
            model=model,
            group=batch.group,
            created_at=batch.created_at,
            request_count=batch.request_count,
            hold_id=hold_id,
        )

    def _send(
        self,
        requests: Mapping[str, LlmRequest],
        provider: str,
        model: str,
        effort: str | None,
        ceiling: int,
        key: str,
    ) -> Batch:
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
            return _run(send())
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            raise self._unavailable(provider, error) from None

    def ended(self, handle: BatchHandle) -> bool:
        built, _ = self._built(handle)
        try:
            return _run(built.batch_status(handle._batch("in_progress"))).status == "ended"
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            raise self._unavailable(handle.provider, error) from None

    def collect(
        self, handle: BatchHandle, output_model: type[BaseModel]
    ) -> dict[str, BatchOutcome]:
        built, base = self._built(handle)
        shape = LlmRequest(system="", messages=[], output_model=output_model)
        ended = handle._batch("ended")
        try:
            results = _run(built.batch_results(ended))
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            raise self._unavailable(handle.provider, error) from None
        outcomes: dict[str, BatchOutcome] = {}
        for result in results:
            outcomes[result.custom_id] = self._outcome(built, base, shape, handle, result)
        if handle.hold_id is not None and not any(
            outcome.status == "succeeded" for outcome in outcomes.values()
        ):
            self._release(handle.hold_id, handle.provider)  # nothing will be recorded
        return outcomes

    def record(
        self, session: Session, handle: BatchHandle, response: LlmResponse, freelancer_id: UUID
    ) -> None:
        """The answer's usage row at the batch price, on `session` and not committed: the
        caller commits it with its own mark that it was recorded, so it is in the books
        once. A model with no price row (a dated alias) is priced as the one the batch was
        sent to. The batch's hold goes in the same transaction (the first answer deletes it,
        the others find it gone), so the estimate is replaced by real rows without a moment
        where neither counts. A failure is raised, not logged: nothing is marked recorded."""
        service = AiConfigService(session, self._settings)
        service.record_usage(
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
        if handle.hold_id is not None:
            service.release(handle.hold_id, commit=False)

    def _reserve(self, provider: str, model: str, estimate_eur: Decimal | None) -> UUID | None:
        """Holds what the batch is expected to cost against the task's daily cap, under the
        lock calls take too, so a call and a batch see each other and a batch cannot
        overshoot the cap by its whole cost (`LlmCapReached` when it does not fit). No
        estimate, no hold: nothing is known to hold."""
        if estimate_eur is None:
            return None
        with self._session_factory() as session:
            return AiConfigService(session, self._settings).reserve(
                self._task, self._purpose, provider, model, estimate_eur, batch=True
            )

    def _release(self, hold_id: UUID, provider: str) -> None:
        """Gives a hold back from a session of its own; one that cannot be given back is
        logged and left to its TTL, since the error that matters is the caller's."""
        try:
            with self._session_factory() as session:
                AiConfigService(session, self._settings).release(hold_id)
        except Exception as error:
            logger.error("%s batch spend hold not released: %s", provider, type(error).__name__)

    def _unavailable(self, provider: str, error: Exception) -> LlmUnavailable:
        status = error.status_code if isinstance(error, ModelHTTPError) else None
        logger.warning("%s batch %s: status %s", provider, type(error).__name__, status)
        return LlmUnavailable(unavailable_sentence(self._task))

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
            settings.update(CACHE_SYSTEM)
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
