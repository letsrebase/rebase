"""The hub's seam onto Claude (REB-508): what one call to `AnthropicCall.complete`
sends, what a refusal and a provider outage turn into, what the tokens count, the
client's retries and timeout, and the three settings the two callers (the card writer,
spec § 5.1, and the proposal engine, § 3.4) share.

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
from pydantic import BaseModel, Field

from rebase_core import llm
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
    json_schema={
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
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_input_tokens: int | None,
        cache_creation_input_tokens: int | None = None,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


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
                "format": {"type": "json_schema", "schema": REQUEST.json_schema},
            },
            "system": REQUEST.system,
            "messages": REQUEST.messages,
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


def test_request_carries_eu_geo_and_leaves_the_timeout_to_the_client() -> None:
    """Inference stays in the EU (spec § 6). The timeout is not the request's: the
    client's holds for each attempt, the retry's included
    (`test_the_sdk_client_is_built_on_first_use`)."""
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
    assert "timeout" not in kwargs
    assert kwargs["inference_geo"] == "eu"


def test_input_tokens_count_the_cache_writes() -> None:
    """A proposal that writes the catalogue's prefix again pays for those tokens as
    input: the row and the event say what was paid, and the cache reads stay apart."""
    message = _Message(
        content=[_Block("text", "{}")],
        stop_reason="end_turn",
        stop_details=None,
        usage=_Usage(
            input_tokens=300,
            output_tokens=90,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=12000,
        ),
        model=MODEL,
    )
    call = AnthropicCall(API_KEY, MODEL, client=StubClient(message))  # type: ignore[arg-type]

    response = call.complete(REQUEST)

    assert response.input_tokens == 12300
    assert response.cache_read_tokens == 0
    assert response.output_tokens == 90

    # A warm cache: nothing written, the prefix read.
    message.usage = _Usage(
        input_tokens=300,
        output_tokens=90,
        cache_read_input_tokens=12000,
        cache_creation_input_tokens=None,
    )
    warm = AnthropicCall(API_KEY, MODEL, client=StubClient(message))  # type: ignore[arg-type]
    read = warm.complete(REQUEST)
    assert (read.input_tokens, read.cache_read_tokens) == (300, 12000)


# ---- the schema, as the API takes it (REB-510) -----------------------------------------


class _Membro(BaseModel):
    nome: str = Field(min_length=1, max_length=40)
    giorni: int = Field(ge=1, le=5)


class _Squadra(BaseModel):
    """A nested model, the shape C4's proposal will have: `$defs`, a `$ref`, a list with
    `maxItems`, a nullable string with `maxLength`."""

    membri: list[_Membro] = Field(max_length=6)
    nota: str | None = Field(max_length=200)


_UNSUPPORTED = {"minLength", "maxLength", "minimum", "maximum", "maxItems"}


def _nodes(node: Any) -> list[dict[str, Any]]:
    """Every schema node under `node`, the maps of names (`properties`, `$defs`) walked
    through rather than read as nodes themselves."""
    if isinstance(node, list):
        return [found for item in node for found in _nodes(item)]
    if not isinstance(node, dict):
        return []
    found = [node]
    for key, value in node.items():
        if key in ("properties", "$defs"):
            found += [inner for sub in value.values() for inner in _nodes(sub)]
        else:
            found += _nodes(value)
    return found


def test_the_schema_sent_carries_only_what_the_api_takes() -> None:
    """The structured-output API refuses length, range and list-size keywords: the seam
    strips them from every schema it sends, at every depth and in `$defs`, and closes
    every object, so a caller hands `Model.model_json_schema()` as it is and validates
    the answer with the same model."""
    message = _Message(
        content=[_Block("text", "{}")],
        stop_reason="end_turn",
        stop_details=None,
        usage=_Usage(input_tokens=1, output_tokens=1, cache_read_input_tokens=0),
        model=MODEL,
    )
    stub = StubClient(message)
    call = AnthropicCall(API_KEY, MODEL, client=stub)  # type: ignore[arg-type]
    raw = _Squadra.model_json_schema()
    assert "$defs" in raw and {"maxItems", "maxLength"} <= {k for n in _nodes(raw) for k in n}

    call.complete(REQUEST.model_copy(update={"json_schema": raw}))

    [kwargs] = stub.beta.messages.calls
    sent = kwargs["output_config"]["format"]["schema"]
    assert "_Membro" in sent["$defs"]
    nodes = _nodes(sent)
    assert not {key for node in nodes for key in node} & _UNSUPPORTED
    objects = [node for node in nodes if node.get("type") == "object"]
    assert len(objects) == 2
    assert all(node["additionalProperties"] is False for node in objects)
    # The caller's own schema is left as it was: it is still what validates the answer.
    assert raw == _Squadra.model_json_schema()


def test_the_sdk_client_is_built_on_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LlmDep` builds an `AnthropicCall` on every wizard post, most of them without a
    CV: constructing one must not open an SDK client nobody uses.

    And the client it builds retries once, with 40 seconds an attempt: the SDK's own
    two retries at 50 seconds each ran a stalled provider to about 151 seconds, past
    the 90 the host vhost gives `/api/hub/team/proposals`."""
    built: list[dict[str, Any]] = []

    def fake_client(**kwargs: Any) -> object:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(llm.anthropic, "Anthropic", fake_client)
    call = AnthropicCall(API_KEY, MODEL)
    assert built == []

    assert call.client is call.client
    assert built == [{"api_key": API_KEY, "max_retries": 1, "timeout": 40.0}]
    # The worst case: two attempts and the half second the SDK waits between them.
    attempts = 1 + built[0]["max_retries"]
    assert attempts * built[0]["timeout"] + 0.5 < 90
