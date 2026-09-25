"""The hub's seam onto Claude (REB-508): what one call to `AnthropicCall.complete`
sends, what a refusal and a provider outage turn into, and the three settings the two
future callers (the card writer, spec § 5.1, and the proposal engine, § 3.4) share.

The network is the only thing faked, the way `test_mail.py` and `test_documenso.py`
fake it: a stub stands in for `anthropic.Anthropic` itself, recording
`beta.messages.create`'s kwargs and answering a canned response, so the request shape,
the refusal path and the three provider exceptions all run for real against
`AnthropicCall`.
"""

import logging
from typing import Any

import anthropic
import httpx2
import pytest

from rebase_core.config import Settings
from rebase_core.errors import DomainError
from rebase_core.llm import (
    AnthropicCall,
    LlmRequest,
    LlmResponse,
    LlmUnavailable,
    RecordingCall,
    call_from_settings,
)

API_KEY = "sk-ant-test-not-a-real-key"
MODEL = "claude-opus-5"
SENTENCE = "Non riesco a proporre un team adesso: riprova tra poco."

REQUEST = LlmRequest(
    system=[
        {
            "type": "text",
            "text": "Scrivi una scheda anonima di un freelance, in italiano.",
            "cache_control": {"type": "ephemeral"},
        }
    ],
    messages=[{"role": "user", "content": "Il CV e la tariffa sono qui sotto."}],
    schema={
        "type": "object",
        "properties": {"riassunto": {"type": "string"}},
        "required": ["riassunto"],
        "additionalProperties": False,
    },
    max_tokens=2000,
)


class _Block:
    """A `content` entry: only `.type` and `.text` are read."""

    def __init__(self, type: str, text: str | None = None) -> None:  # noqa: A002
        self.type = type
        self.text = text


class _StopDetails:
    def __init__(self, category: str | None) -> None:
        self.category = category


class _Usage:
    def __init__(
        self, input_tokens: int, output_tokens: int, cache_read_input_tokens: int | None
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class _Message:
    """The canned answer `beta.messages.create` gives back: the same four fields
    `AnthropicCall.complete` reads off a real `BetaMessage`."""

    def __init__(
        self,
        content: list[_Block],
        stop_reason: str,
        stop_details: _StopDetails | None,
        usage: _Usage,
        model: str,
    ) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.usage = usage
        self.model = model


class _Messages:
    def __init__(self, response: _Message | BaseException) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response


class _Beta:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


class StubClient:
    """Stands in for `anthropic.Anthropic`: only `beta.messages.create` exists, no
    network. Handed to `AnthropicCall` through its keyword-only `client` override."""

    def __init__(self, response: _Message | BaseException) -> None:
        self.beta = _Beta(_Messages(response))


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_request())


def _rate_limit_error() -> anthropic.RateLimitError:
    body = {"error": {"type": "rate_limit_error", "message": "slow down"}}
    response = httpx2.Response(429, request=_request(), json=body)
    return anthropic.RateLimitError("slow down", response=response, body=body)


def _status_error() -> anthropic.APIStatusError:
    body = {"error": {"type": "api_error", "message": "server error"}}
    response = httpx2.Response(500, request=_request(), json=body)
    return anthropic.APIStatusError("server error", response=response, body=body)


def test_complete_sends_the_documented_request() -> None:
    message = _Message(
        content=[_Block("text", "Ecco la scheda.")],
        stop_reason="end_turn",
        stop_details=None,
        usage=_Usage(input_tokens=120, output_tokens=340, cache_read_input_tokens=80),
        model=MODEL,
    )
    stub = StubClient(message)
    call = AnthropicCall(API_KEY, MODEL, client=stub)  # type: ignore[arg-type]

    response = call.complete(REQUEST)

    assert response == LlmResponse(
        text="Ecco la scheda.",
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=120,
        output_tokens=340,
        cache_read_tokens=80,
    )
    # Exactly the documented kwargs: no `temperature`, no `budget_tokens`, no
    # prefill, no `tool_choice`.
    assert stub.beta.messages.calls == [
        {
            "model": MODEL,
            "max_tokens": 2000,
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": "medium",
                "format": {"type": "json_schema", "schema": REQUEST.schema},
            },
            "system": REQUEST.system,
            "messages": REQUEST.messages,
            "timeout": 50.0,
            "inference_geo": "eu",
        }
    ]


def test_refusal_answers_no_text() -> None:
    message = _Message(
        content=[],
        stop_reason="refusal",
        stop_details=_StopDetails(category="cyber"),
        usage=_Usage(input_tokens=90, output_tokens=5, cache_read_input_tokens=None),
        model=MODEL,
    )
    stub = StubClient(message)
    call = AnthropicCall(API_KEY, MODEL, client=stub)  # type: ignore[arg-type]

    response = call.complete(REQUEST)

    assert response.text is None
    assert response.stop_reason == "refusal"
    assert response.refusal_category == "cyber"
    # `cache_read_input_tokens` was `None`: the response still carries a plain `int`.
    assert response.cache_read_tokens == 0


@pytest.mark.parametrize(
    "error",
    [_connection_error(), _rate_limit_error(), _status_error()],
    ids=["connection", "rate_limit", "status"],
)
def test_provider_errors_become_unavailable(
    error: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    # A DB-backed test earlier in the session runs `upgrade_to_head`, whose Alembic
    # `env.py` calls `fileConfig`, which disables every logger not in `alembic.ini`'s
    # own `[loggers]` list -- this module's among them (the same trap
    # `test_documenso_webhook_api.py` and `test_member_api.py` document). Undo it here
    # so `caplog` can see what this test is about.
    logging.getLogger("rebase_core.llm").disabled = False
    stub = StubClient(error)
    call = AnthropicCall(API_KEY, MODEL, client=stub)  # type: ignore[arg-type]

    with (
        caplog.at_level(logging.WARNING, logger="rebase_core.llm"),
        pytest.raises(LlmUnavailable) as exc_info,
    ):
        call.complete(REQUEST)

    assert str(exc_info.value) == SENTENCE
    [record] = caplog.records
    logged = record.getMessage()
    assert type(error).__name__ in logged
    # Never the request: no prompt text, no key, nothing that names a person.
    assert "CV" not in logged
    assert API_KEY not in logged


def test_settings_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.anthropic_api_key == ""
    assert settings.team_builder_model == "claude-opus-5"
    assert settings.team_builder_enabled is True
    assert call_from_settings(settings) is None

    with_key = Settings(anthropic_api_key="sk-ant-test", _env_file=None)  # type: ignore[call-arg]
    call = call_from_settings(with_key)
    assert isinstance(call, AnthropicCall)
    assert call.model == "claude-opus-5"


def test_provider_errors_are_domain_errors() -> None:
    """The API's `domain_error_handler` and the MCP's `_call` both catch `DomainError`,
    not `LlmUnavailable` by name: this is what turns a stalled provider into a 502 or a
    tool sentence instead of a traceback."""
    assert issubclass(LlmUnavailable, DomainError)
    stub = StubClient(_connection_error())
    call = AnthropicCall(API_KEY, MODEL, client=stub)  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        call.complete(REQUEST)

    assert exc_info.value.message == SENTENCE


def test_recording_call_keeps_requests() -> None:
    first = LlmResponse(
        text="Uno",
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
    )
    second = LlmResponse(
        text="Due",
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=2,
        output_tokens=2,
        cache_read_tokens=0,
    )
    call = RecordingCall([first, second])

    assert call.complete(REQUEST) is first
    assert call.complete(REQUEST) is second
    assert call.requests == [REQUEST, REQUEST]


def test_request_carries_timeout_and_eu_geo() -> None:
    """Under nginx's own 60-second cut, and inference stays in the EU (spec § 6)."""
    message = _Message(
        content=[_Block("text", "Ecco la scheda.")],
        stop_reason="end_turn",
        stop_details=None,
        usage=_Usage(input_tokens=1, output_tokens=1, cache_read_input_tokens=0),
        model=MODEL,
    )
    stub = StubClient(message)
    call = AnthropicCall(API_KEY, MODEL, client=stub)  # type: ignore[arg-type]

    call.complete(REQUEST)

    [kwargs] = stub.beta.messages.calls
    assert kwargs["timeout"] == 50.0
    assert kwargs["inference_geo"] == "eu"
