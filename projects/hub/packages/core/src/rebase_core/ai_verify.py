"""`rebase ai-verify`: what a provider's model does with the seam's call, checked with a fixed
prompt that carries no personal data (REB-840).

The seam asks every provider the same thing: a native structured output, with the task's
effort setting. Only Anthropic has been exercised against the real API; this sends the
smallest such call to OpenAI or Google with the key an admin saved, and reports each
capability on its own so that a failure names what to fix. It never prints the key, nor a
provider's error text (which may echo a fragment of it): a failure is the exception's class
and the HTTP status."""

from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, ConfigDict
from pydantic_ai.direct import model_request
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UserError
from pydantic_ai.settings import ModelSettings

from rebase_core.llm import (
    LlmMessage,
    LlmRequest,
    ModelFactory,
    _history,
    _parameters,
    _run,
    build_model,
)

PROMPT = "Name the capital city of France and the country it is the capital of."
_AUTH_STATUSES = frozenset({401, 403})
_MAX_TOKENS = 512
# Anything the three providers accept as an effort: «thinking_ok» is that the setting is
# taken, not that the model reasons at length.
_EFFORT = "low"


class VerifyAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    country: str


@dataclass(frozen=True)
class VerifyReport:
    key_injected: bool  # the provider took the key: no 401 or 403 and an answer came back
    native_output_ok: bool  # the answer is a `VerifyAnswer` the provider's schema mode produced
    thinking_ok: bool  # the same call with the effort setting is accepted
    usage_present: bool  # the answer says how many tokens it took: the cost depends on it
    error: str | None

    def lines(self) -> list[str]:
        flags = (
            f"key_injected={self.key_injected} native_output_ok={self.native_output_ok} "
            f"thinking_ok={self.thinking_ok} usage_present={self.usage_present}"
        )
        return [flags] if self.error is None else [flags, f"error={self.error}"]

    @property
    def ok(self) -> bool:
        return self.key_injected and self.native_output_ok and self.thinking_ok


@dataclass(frozen=True)
class _Attempt:
    reached: bool
    status: int | None = None
    native: bool = False
    usage: bool = False
    error: str | None = None


def _describe(error: Exception) -> str:
    status = error.status_code if isinstance(error, ModelHTTPError) else None
    return f"{type(error).__name__}" + (f" (status {status})" if status is not None else "")


def verify(
    provider: str,
    model: str,
    key: str,
    *,
    model_factory: ModelFactory = build_model,
) -> VerifyReport:
    """Two calls: the plain one (key, native output, usage), then the one with the effort
    setting, which is only tried once the plain one has been answered."""
    request = LlmRequest(
        system="",
        messages=[LlmMessage(role="user", text=PROMPT)],
        output_model=VerifyAnswer,
    )

    def ask(effort: str | None) -> _Attempt:
        instance, settings = model_factory(provider, model, key, effort)
        try:
            answer = _run(
                model_request(
                    instance,
                    _history(request),
                    model_settings=cast(ModelSettings, {**settings, "max_tokens": _MAX_TOKENS}),
                    model_request_parameters=_parameters(request),
                )
            )
        except (ModelAPIError, UnexpectedModelBehavior, UserError) as error:
            status = error.status_code if isinstance(error, ModelHTTPError) else None
            return _Attempt(reached=False, status=status, error=_describe(error))
        try:
            VerifyAnswer.model_validate_json(answer.text or "")
            native = True
        except ValueError:
            native = False
        return _Attempt(
            reached=True,
            native=native,
            usage=answer.usage.input_tokens > 0 and answer.usage.output_tokens > 0,
            error=None if native else "the answer is not the schema",
        )

    plain = ask(None)
    # A refusal that is not 401 or 403 (a 400 on the schema, a 404 on the model name) was
    # still made by an account the provider let in: the key is injected, something else is wrong.
    injected = plain.reached or (plain.status is not None and plain.status not in _AUTH_STATUSES)
    if not plain.reached:
        return VerifyReport(
            key_injected=injected,
            native_output_ok=False,
            thinking_ok=False,
            usage_present=False,
            error=plain.error,
        )
    effort = ask(_EFFORT)
    return VerifyReport(
        key_injected=True,
        native_output_ok=plain.native,
        thinking_ok=effort.reached,
        usage_present=plain.usage,
        error=plain.error or effort.error,
    )
