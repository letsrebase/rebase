"""The hub's seam onto a language model (REB-508, REB-831): what `AiCall.complete` sends to
Anthropic, over the wire, and what comes back; what refuses a call before any network; what
a refusal, an outage and a failure turn into; what the tokens count; and the settings each
provider is built with.

Two kinds of fake, as `test_mail.py` and `test_documenso.py` do for their own seams. The
Anthropic path runs for real, from `AiCall` through Pydantic AI and the Anthropic SDK, with
only the network replaced by an `httpx2.MockTransport` that records the request and answers a
canned `BetaMessage`: that is where the beta header, `fallbacks`, `inference_geo`, adaptive
thinking, the effort, `cache_control` and the transformed schema are asserted, as bytes on the
wire. Everything about gating and recording runs against a stub Pydantic AI model handed
through `model_factory`.
"""

import asyncio
import base64
import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.ai_config import CAP_REACHED, NO_PRICE, AiConfigService
from rebase_core.config import Settings
from rebase_core.db import SessionOpener
from rebase_core.errors import (
    DomainError,
    LlmCapReached,
    LlmNotConfigured,
    LlmUnavailable,
)
from rebase_core.llm import (
    FALLBACK_BETA,
    UNAVAILABLE_SENTENCE,
    AiCall,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    RecordingCall,
    TaskOverride,
    build_model,
    call_for_task,
)
from rebase_core.models import AiTask, AiUsage, User

API_KEY = "sk-ant-test-not-a-real-key"
SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()
MODEL = "claude-opus-5"
NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
RULES = "Scrivi una scheda anonima di un freelance, in italiano."
CATALOGUE = "<catalogo>\nt1: backend, senior\n</catalogo>"
USER_TEXT = "Il CV e la tariffa sono qui sotto."


class Scheda(BaseModel):
    """A docstring written for whoever maintains the model: it never reaches the provider."""

    model_config = ConfigDict(extra="forbid")

    riassunto: str = Field(min_length=1, max_length=40)
    anni: int | None = Field(ge=0, le=60)


ANSWER = '{"riassunto": "Sviluppatore backend", "anni": 7}'


def _request(**changes: Any) -> LlmRequest:
    fields: dict[str, Any] = {
        "system": RULES,
        "messages": [LlmMessage(role="user", text=USER_TEXT)],
        "output_model": Scheda,
    }
    return LlmRequest(**{**fields, **changes})


def _settings() -> Settings:
    return Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]


# ---- the database: a task that can be called --------------------------------------------


@pytest.fixture
def opener(hub_engine: Engine) -> Iterator[SessionOpener]:
    """A session opener over the worker's database with the card task callable: key saved,
    Anthropic on, a price for `claude-opus-5`. Wiped back to what migration 0036 seeded."""

    def open_session() -> Any:
        return closing(Session(hub_engine))

    with open_session() as session:
        admin = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
        session.add(admin)
        session.commit()
        service = AiConfigService(session, _settings(), now=lambda: NOON)
        service.save_key(admin.id, "anthropic", API_KEY)
        service.set_provider_enabled(admin.id, "anthropic", True)
        service.upsert_price(admin.id, "anthropic", MODEL, Decimal("5"), Decimal("25"))
    yield open_session
    with open_session() as session:
        for statement in (
            "DELETE FROM ai_usage",
            "DELETE FROM ai_prices",
            "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, "
            "key_last4 = NULL, saved_by = NULL, saved_at = NULL, enabled = false",
            "UPDATE ai_tasks SET provider = 'anthropic', model = 'claude-opus-5', "
            "effort = 'medium', daily_cap_eur = NULL, enabled = true, "
            "max_output_tokens = CASE task WHEN 'card' THEN 2000 ELSE 8000 END",
            "DELETE FROM admin_actions",
            "DELETE FROM users",
        ):
            session.execute(text(statement))
        session.commit()


def _usage_rows(opener: SessionOpener) -> list[AiUsage]:
    with opener() as session:
        return list(session.scalars(select(AiUsage).order_by(AiUsage.at)))


# ---- a stub model, for everything that is not the wire ----------------------------------


class Stub:
    """A Pydantic AI model that answers `answers` in order and records what `build_model`
    was asked for, handed to `AiCall` through `model_factory`."""

    def __init__(self, *answers: ModelResponse | Exception) -> None:
        self.answers = list(answers or [self.reply()])
        self.built: list[tuple[str, str, str, str | None]] = []
        self.seen: list[list[ModelMessage]] = []
        self.model_settings: list[ModelSettings | None] = []

    @staticmethod
    def reply(
        body: str = ANSWER, *, input_tokens: int = 1200, output_tokens: int = 180, **usage: int
    ) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content=body)],
            usage=RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens, **usage),
        )

    def factory(
        self, provider: str, model: str, api_key: str, effort: str | None
    ) -> tuple[Model, ModelSettings]:
        self.built.append((provider, model, api_key, effort))

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.seen.append(messages)
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return FunctionModel(respond, model_name=model), ModelSettings()


def _call(opener: SessionOpener, stub: Stub, **options: Any) -> AiCall:
    return AiCall(opener, _settings(), "card", model_factory=stub.factory, **options)


def test_complete_returns_parsed_and_usage_and_records_row(opener: SessionOpener) -> None:
    stub = Stub(Stub.reply(input_tokens=1500, output_tokens=180, cache_read_tokens=1000))
    response = _call(opener, stub, freelancer_id=None).complete(_request())

    assert response.parsed == Scheda(riassunto="Sviluppatore backend", anni=7)
    assert response.text == ANSWER
    assert (response.stop_reason, response.refusal_category) == ("end_turn", None)
    assert response.provider == "anthropic"
    # `input_tokens` is what was not read from a cache; the cache reads are counted apart.
    assert (response.input_tokens, response.output_tokens, response.cache_read_tokens) == (
        500,
        180,
        1000,
    )
    [row] = _usage_rows(opener)
    assert (row.task, row.purpose, row.provider, row.model) == ("card", "run", "anthropic", MODEL)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens) == (500, 180, 1000)
    assert row.priced is True
    assert row.cost_eur == Decimal("0.0025") + Decimal("0.0045") + Decimal("0.0050")


def test_thinking_tokens_are_counted_apart_from_output(opener: SessionOpener) -> None:
    stub = Stub(Stub.reply(input_tokens=100, output_tokens=900, output_reasoning_tokens=700))
    response = _call(opener, stub).complete(_request())

    assert (response.output_tokens, response.thinking_tokens) == (200, 700)
    [row] = _usage_rows(opener)
    assert (row.output_tokens, row.thinking_tokens) == (200, 700)


def test_purpose_and_freelancer_are_recorded(opener: SessionOpener) -> None:
    stub = Stub()
    _call(opener, stub, purpose="prova").complete(_request())

    [row] = _usage_rows(opener)
    assert (row.task, row.purpose, row.freelancer_id) == ("card", "prova", None)


def test_a_prova_consumes_the_tasks_daily_cap(opener: SessionOpener) -> None:
    with opener() as session:
        task = session.get(AiTask, "card")
        assert task is not None
        task.daily_cap_eur = Decimal("0.01")
        session.commit()
    stub = Stub(Stub.reply(input_tokens=1500, output_tokens=180), Stub.reply())

    _call(opener, stub, purpose="prova").complete(_request())
    with pytest.raises(LlmCapReached) as error:
        _call(opener, stub).complete(_request())

    assert str(error.value) == CAP_REACHED
    assert len(stub.built) == 1  # the second call never reached the provider


def test_key_read_from_database_each_call(opener: SessionOpener) -> None:
    stub = Stub(Stub.reply(), Stub.reply())
    call = _call(opener, stub)
    call.complete(_request())
    with opener() as session:
        admin = session.scalars(select(User)).one()
        AiConfigService(session, _settings()).save_key(admin.id, "anthropic", "sk-ant-rotated")
    call.complete(_request())

    assert [built[2] for built in stub.built] == [API_KEY, "sk-ant-rotated"]


def test_task_row_is_read_each_call(opener: SessionOpener) -> None:
    stub = Stub(Stub.reply(), Stub.reply())
    call = _call(opener, stub)
    call.complete(_request())
    with opener() as session:
        task = session.get(AiTask, "card")
        assert task is not None
        task.effort = "high"
        session.commit()
    call.complete(_request())

    assert [built[3] for built in stub.built] == ["medium", "high"]


def test_no_key_returns_none_from_call_for_task(opener: SessionOpener) -> None:
    with opener() as session:
        session.execute(
            text(
                "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, key_last4 = NULL"
            )
        )
        session.commit()

    assert call_for_task(opener, _settings(), "card") is None


def test_disabled_provider_returns_none(opener: SessionOpener) -> None:
    with opener() as session:
        session.execute(text("UPDATE ai_providers SET enabled = false"))
        session.commit()

    assert call_for_task(opener, _settings(), "card") is None


def test_disabled_task_returns_none(opener: SessionOpener) -> None:
    with opener() as session:
        session.execute(text("UPDATE ai_tasks SET enabled = false WHERE task = 'card'"))
        session.commit()

    assert call_for_task(opener, _settings(), "card") is None
    assert isinstance(call_for_task(opener, _settings(), "team_proposal"), AiCall)


def test_a_configured_task_gives_a_call(opener: SessionOpener) -> None:
    assert isinstance(call_for_task(opener, _settings(), "card"), AiCall)


def test_cap_reached_raises_before_provider_is_built(opener: SessionOpener) -> None:
    with opener() as session:
        task = session.get(AiTask, "card")
        assert task is not None
        task.daily_cap_eur = Decimal("0.01")
        session.commit()
        session.add(
            AiUsage(
                at=datetime.now(UTC),
                task="card",
                purpose="run",
                provider="anthropic",
                model=MODEL,
                cost_eur=Decimal("0.02"),
                priced=True,
            )
        )
        session.commit()
    stub = Stub()

    with pytest.raises(LlmCapReached):
        _call(opener, stub).complete(_request())

    assert stub.built == []
    assert len(_usage_rows(opener)) == 1  # nothing was recorded for the refused call


def test_missing_price_refuses_before_provider_is_built(opener: SessionOpener) -> None:
    with opener() as session:
        session.execute(text("DELETE FROM ai_prices"))
        session.commit()
    stub = Stub()

    with pytest.raises(LlmNotConfigured) as error:
        _call(opener, stub).complete(_request())

    assert str(error.value) == NO_PRICE
    assert stub.built == []


def test_a_secrets_key_that_does_not_open_the_key_refuses_without_key_text(
    opener: SessionOpener,
) -> None:
    other = base64.b64encode(os.urandom(32)).decode()
    stub = Stub()
    call = AiCall(
        opener,
        Settings(secrets_key=other, _env_file=None),  # type: ignore[call-arg]
        "card",
        model_factory=stub.factory,
    )

    with pytest.raises(LlmNotConfigured) as error:
        call.complete(_request())

    assert API_KEY not in str(error.value)
    assert "REBASE_SECRETS_KEY" in str(error.value)
    assert stub.built == []


def test_unpriced_history_still_reads(opener: SessionOpener) -> None:
    """A usage row with no price stays a row, shown as unpriced; it never lets a call
    through that the price check refused."""
    with opener() as session:
        session.add(
            AiUsage(
                at=NOON,
                task="card",
                purpose="run",
                provider="openai",
                model="gpt-5",
                priced=False,
                cost_eur=None,
            )
        )
        session.execute(text("DELETE FROM ai_prices"))
        session.commit()
        read = AiConfigService(session, _settings(), now=lambda: NOON).read()
    assert [(row.task, row.unpriced_calls) for row in read.spend.rows] == [("card", 1)]

    with pytest.raises(LlmNotConfigured):
        _call(opener, Stub()).complete(_request())


def test_provider_error_maps_to_llm_unavailable_without_key_in_message(
    opener: SessionOpener, caplog: pytest.LogCaptureFixture
) -> None:
    error = ModelHTTPError(429, MODEL, {"error": f"quota exceeded for {API_KEY}"})
    stub = Stub(error)

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(LlmUnavailable) as exc_info,
    ):
        _call(opener, stub).complete(_request())

    assert str(exc_info.value) == UNAVAILABLE_SENTENCE
    assert exc_info.value.__cause__ is None and exc_info.value.__suppress_context__
    assert API_KEY not in caplog.text
    assert "CV" not in caplog.text
    assert "429" in caplog.text
    assert _usage_rows(opener) == []  # a call that failed spent nothing we know of


def test_a_usage_row_that_cannot_be_written_does_not_lose_the_paid_answer(
    opener: SessionOpener, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(self: AiConfigService, **kwargs: Any) -> None:
        raise RuntimeError(f"disk full, key {API_KEY}")

    monkeypatch.setattr(AiConfigService, "record_usage", broken)

    with caplog.at_level(logging.INFO):
        response = _call(opener, Stub()).complete(_request())

    assert response.parsed is not None
    assert "usage not recorded: RuntimeError" in caplog.text
    assert API_KEY not in caplog.text


def test_provider_errors_are_domain_errors(opener: SessionOpener) -> None:
    """The API's `domain_error_handler` and the MCP's `_call` both catch `DomainError`,
    not `LlmUnavailable` by name: this is what turns a stalled provider into a 502 or a
    tool sentence instead of a traceback."""
    assert issubclass(LlmUnavailable, DomainError)
    assert issubclass(LlmCapReached, LlmUnavailable)
    assert issubclass(LlmNotConfigured, LlmUnavailable)
    with pytest.raises(DomainError):
        _call(opener, Stub(ModelHTTPError(503, MODEL, None))).complete(_request())


def test_refusal_gives_text_none_and_category(opener: SessionOpener) -> None:
    refusal = ModelResponse(
        parts=[],
        usage=RequestUsage(input_tokens=300, output_tokens=0),
        finish_reason="content_filter",
        provider_details={"finish_reason": "refusal", "refusal_category": "cyber"},
    )

    response = _call(opener, Stub(refusal)).complete(_request())

    assert (response.text, response.parsed) == (None, None)
    assert (response.stop_reason, response.refusal_category) == ("refusal", "cyber")
    assert len(_usage_rows(opener)) == 1  # a refusal was still paid for


def test_max_tokens_is_the_callers_word(opener: SessionOpener) -> None:
    cut = ModelResponse(
        parts=[TextPart(content='{"riassunto": "Sviluppa')],
        usage=RequestUsage(input_tokens=300, output_tokens=2000),
        finish_reason="length",
    )

    response = _call(opener, Stub(cut)).complete(_request())

    assert response.stop_reason == "max_tokens"
    assert response.parsed is None


def test_an_answer_that_is_not_the_shape_keeps_its_text_and_has_no_parsed(
    opener: SessionOpener,
) -> None:
    too_long = json.dumps({"riassunto": "x" * 41, "anni": 3})

    response = _call(opener, Stub(Stub.reply(too_long))).complete(_request())

    assert response.text == too_long
    assert response.parsed is None


def test_request_reaches_the_model_as_instructions_and_history(opener: SessionOpener) -> None:
    stub = Stub()
    _call(opener, stub).complete(
        _request(
            system_cached=CATALOGUE,
            messages=[
                LlmMessage(role="user", text=USER_TEXT),
                LlmMessage(role="assistant", text=ANSWER),
                LlmMessage(role="user", text="Ancora, in forma neutra."),
            ],
        )
    )

    [seen] = stub.seen
    kinds = [type(message).__name__ for message in seen]
    assert kinds == ["ModelRequest", "ModelResponse", "ModelRequest"]


def test_override_is_called_as_the_task_but_goes_to_its_own_provider(
    opener: SessionOpener,
) -> None:
    with opener() as session:
        admin = session.scalars(select(User)).one()
        service = AiConfigService(session, _settings())
        service.upsert_price(admin.id, "anthropic", "claude-opus-5-5", Decimal("5"), Decimal("25"))
    stub = Stub()
    call = _call(
        opener, stub, override=TaskOverride("anthropic", "claude-opus-5-5", "high"), purpose="eval"
    )

    call.complete(_request())

    assert stub.built == [("anthropic", "claude-opus-5-5", API_KEY, "high")]
    [row] = _usage_rows(opener)
    assert (row.task, row.purpose, row.model) == ("card", "eval", "claude-opus-5-5")


def test_override_without_a_price_or_with_the_provider_off_is_refused(
    opener: SessionOpener,
) -> None:
    stub = Stub()
    with pytest.raises(LlmNotConfigured):
        _call(opener, stub, override=TaskOverride("anthropic", "claude-unpriced")).complete(
            _request()
        )
    with pytest.raises(LlmNotConfigured):
        _call(opener, stub, override=TaskOverride("openai", "gpt-5")).complete(_request())
    assert stub.built == []


def test_a_caller_inside_a_running_loop_still_gets_its_answer(opener: SessionOpener) -> None:
    """The MCP's tools are sync functions run by an event loop; `asyncio.run` alone
    refuses to start there."""
    stub = Stub()

    async def tool() -> LlmResponse:
        return _call(opener, stub).complete(_request())

    assert asyncio.run(tool()).parsed == Scheda(riassunto="Sviluppatore backend", anni=7)


# ---- build_model ------------------------------------------------------------------------


def test_build_model_maps_effort_per_provider() -> None:
    _, anthropic_settings = build_model("anthropic", MODEL, API_KEY, "medium")
    assert anthropic_settings["anthropic_thinking"] == {"type": "adaptive"}  # type: ignore[typeddict-item]
    assert anthropic_settings["anthropic_effort"] == "medium"  # type: ignore[typeddict-item]
    assert anthropic_settings["anthropic_betas"] == [FALLBACK_BETA]  # type: ignore[typeddict-item]
    assert anthropic_settings["extra_body"] == {  # type: ignore[typeddict-item]
        "fallbacks": "default",
        "inference_geo": "global",
    }

    _, openai_settings = build_model("openai", "gpt-5", "sk-openai", "high")
    assert openai_settings["openai_reasoning_effort"] == "high"  # type: ignore[typeddict-item]

    _, google_settings = build_model("google", "gemini-3-pro-preview", "g-key", "low")
    assert google_settings["google_thinking_config"] == {"thinking_level": "LOW"}  # type: ignore[typeddict-item]


def test_build_model_without_effort_leaves_the_providers_default() -> None:
    _, anthropic_settings = build_model("anthropic", MODEL, API_KEY, None)
    assert "anthropic_effort" not in anthropic_settings
    _, openai_settings = build_model("openai", "gpt-5", "sk-openai", None)
    assert "openai_reasoning_effort" not in openai_settings
    _, google_settings = build_model("google", "gemini-3-pro-preview", "g-key", None)
    assert "google_thinking_config" not in google_settings


def test_timeout_and_retry_ceiling() -> None:
    """Two attempts of 40 seconds end near 81, inside the 90 the host vhost gives the
    team builder (REB-581): the constants of the file this replaced, kept for every
    provider."""
    for provider in ("anthropic", "openai", "google"):
        _, settings = build_model(provider, "m", "key", None)
        assert settings["timeout"] == 40.0
        assert settings["max_retries"] == 1


def test_build_model_injects_api_key_not_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    model, _ = build_model("anthropic", MODEL, API_KEY, None)
    assert model.client.api_key == API_KEY  # type: ignore[attr-defined]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-the-environment")
    model, _ = build_model("anthropic", MODEL, API_KEY, None)
    assert model.client.api_key == API_KEY  # type: ignore[attr-defined]

    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
    openai_model, _ = build_model("openai", "gpt-5", "sk-openai", None)
    assert openai_model.client.api_key == "sk-openai"  # type: ignore[attr-defined]

    monkeypatch.setenv("GOOGLE_API_KEY", "g-from-the-environment")
    monkeypatch.setenv("GEMINI_API_KEY", "g-from-the-environment")
    google_model, _ = build_model("google", "gemini-3-pro-preview", "g-key", None)
    assert google_model.client._api_client.api_key == "g-key"  # type: ignore[attr-defined]


def test_unknown_provider_is_not_configured() -> None:
    with pytest.raises(LlmNotConfigured):
        build_model("mistral", "m", "key", None)


# ---- the Anthropic wire -----------------------------------------------------------------


def _beta_message(
    content: list[dict[str, Any]] | None = None,
    *,
    stop_reason: str = "end_turn",
    model: str = MODEL,
    usage: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content if content is not None else [{"type": "text", "text": ANSWER}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage
        or {
            "input_tokens": 400,
            "output_tokens": 180,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 1000,
        },
        **extra,
    }


class Wire:
    """`api.anthropic.com` as an `httpx2.MockTransport`: every request recorded, answered by
    `handler`. Installed in place of the provider's own HTTP client."""

    def __init__(self, handler: Callable[[httpx2.Request], httpx2.Response] | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self._handler = handler or (lambda request: httpx2.Response(200, json=_beta_message()))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._handler(request)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    from pydantic_ai.providers import anthropic as provider_module

    wire = Wire()
    real = provider_module.AnthropicProvider

    def provider(*, api_key: str) -> Any:
        return real(
            api_key=api_key, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(wire))
        )

    monkeypatch.setattr(provider_module, "AnthropicProvider", provider)
    return wire


def _wire_call(opener: SessionOpener, **options: Any) -> AiCall:
    return AiCall(opener, _settings(), "card", **options)


def _expected_schema() -> dict[str, Any]:
    raw = {k: v for k, v in Scheda.model_json_schema().items() if k != "description"}
    return _without_titles(anthropic.transform_schema(raw))


def _without_titles(node: Any) -> Any:
    """Pydantic AI drops every `title` the schema carries, as cosmetic; the SDK's
    `transform_schema` keeps them. Nothing else differs from the SDK's own output."""
    if isinstance(node, dict):
        return {k: _without_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_without_titles(v) for v in node]
    return node


def test_wire_carries_every_anthropic_feature_the_call_always_had(
    opener: SessionOpener, wire: Wire
) -> None:
    response = _wire_call(opener).complete(_request())

    [request] = wire.requests
    body = wire.body
    # The server-side fallback: the beta header and `fallbacks`, never the array form.
    assert FALLBACK_BETA in request.headers["anthropic-beta"].split(",")
    assert body["fallbacks"] == "default"
    assert body["inference_geo"] == "global"
    # Adaptive thinking at the task's effort.
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    # The schema, through the SDK's `transform_schema`: limits moved into descriptions,
    # objects closed, and the model's docstring left out.
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": _expected_schema()}
    assert "maintains" not in json.dumps(body["output_config"])
    assert '"maxLength"' not in json.dumps(body["output_config"])  # a key, not a note
    assert body["model"] == MODEL
    assert body["max_tokens"] == 2000  # the seeded ceiling of the card task
    assert request.headers["authorization"] == f"Bearer {API_KEY}"  # how SDK 2.x sends a key
    # Text goes as the caller wrote it: one system block, one user message.
    assert body["system"] == [{"type": "text", "text": RULES}]
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": USER_TEXT}]}]

    assert response.parsed == Scheda(riassunto="Sviluppatore backend", anni=7)
    assert (response.input_tokens, response.cache_read_tokens) == (500, 1000)


def test_wire_marks_the_catalogue_block_for_caching(opener: SessionOpener, wire: Wire) -> None:
    _wire_call(opener).complete(_request(system_cached=CATALOGUE))

    assert wire.body["system"] == [
        {"type": "text", "text": RULES},
        {
            "type": "text",
            "text": CATALOGUE,
            "cache_control": {"type": "ephemeral", "ttl": "5m"},  # the default ttl, spelled out
        },
    ]


def test_wire_sends_no_empty_system_block(opener: SessionOpener, wire: Wire) -> None:
    """A caller with no rules (a probe) must not send `system: [{"text": ""}]`, which
    Anthropic refuses; with a cached block only that block goes, and with neither, none."""
    _wire_call(opener).complete(_request(system=""))
    assert "system" not in wire.body

    _wire_call(opener).complete(_request(system="  ", system_cached=CATALOGUE))
    assert [block["text"] for block in wire.body["system"]] == [CATALOGUE]


def test_wire_sends_no_cache_control_without_a_cached_block(
    opener: SessionOpener, wire: Wire
) -> None:
    _wire_call(opener).complete(_request())

    assert "cache_control" not in json.dumps(wire.body)


def test_wire_carries_a_neutral_retry_as_a_second_turn(opener: SessionOpener, wire: Wire) -> None:
    _wire_call(opener).complete(
        _request(
            messages=[
                LlmMessage(role="user", text=USER_TEXT),
                LlmMessage(role="assistant", text=ANSWER),
                LlmMessage(role="user", text="Riscrivila."),
            ]
        )
    )

    assert [(m["role"], m["content"][0]["text"]) for m in wire.body["messages"]] == [
        ("user", USER_TEXT),
        ("assistant", ANSWER),
        ("user", "Riscrivila."),
    ]


def test_wire_output_ceiling_is_the_tasks_unless_the_caller_asks_for_less(
    opener: SessionOpener, wire: Wire
) -> None:
    with opener() as session:
        session.execute(text("UPDATE ai_tasks SET max_output_tokens = 3000 WHERE task = 'card'"))
        session.commit()

    _wire_call(opener).complete(_request())
    assert wire.body["max_tokens"] == 3000
    _wire_call(opener).complete(_request(max_tokens=50))
    assert wire.body["max_tokens"] == 50
    _wire_call(opener).complete(_request(max_tokens=90_000))
    assert wire.body["max_tokens"] == 3000


def test_wire_effort_follows_the_task(opener: SessionOpener, wire: Wire) -> None:
    with opener() as session:
        session.execute(text("UPDATE ai_tasks SET effort = 'high' WHERE task = 'card'"))
        session.commit()

    _wire_call(opener).complete(_request())

    assert wire.body["output_config"]["effort"] == "high"


def test_wire_refusal(opener: SessionOpener, wire: Wire) -> None:
    wire._handler = lambda request: httpx2.Response(
        200,
        json=_beta_message(
            [],
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": None},
        ),
    )

    response = _wire_call(opener).complete(_request())

    assert (response.text, response.stop_reason, response.refusal_category) == (
        None,
        "refusal",
        "cyber",
    )


def test_wire_fallback_block_is_read_as_the_model_that_answered(
    opener: SessionOpener, wire: Wire
) -> None:
    """When the requested model declines and the server falls back, `content` carries a
    `fallback` block between the two models' output; Pydantic AI alone raises on a block
    it does not know. What the caller gets is what the answering model wrote, and the
    model's name is that model's."""
    wire._handler = lambda request: httpx2.Response(
        200,
        json=_beta_message(
            [
                {"type": "text", "text": '{"riassunto": "parzia'},
                {
                    "type": "fallback",
                    "from": {"model": MODEL},
                    "to": {"model": "claude-opus-5-5"},
                    "trigger": {"type": "refusal"},
                },
                {"type": "text", "text": ANSWER},
            ],
            model="claude-opus-5-5",
        ),
    )

    response = _wire_call(opener).complete(_request())

    assert response.parsed == Scheda(riassunto="Sviluppatore backend", anni=7)
    assert response.model == "claude-opus-5-5"
    [row] = _usage_rows(opener)
    assert row.model == "claude-opus-5-5"
    # No price row for the fallback model: priced as `claude-opus-5`, the one asked for.
    assert row.priced is True and row.cost_eur is not None and row.cost_eur > 0


def test_an_answer_from_an_unpriced_model_still_spends_the_daily_cap(
    opener: SessionOpener, wire: Wire
) -> None:
    """Review of #666, Major 2: a fallback model or a dated alias with no price row used to
    be recorded unpriced, cost NULL, so the cap never moved."""
    with opener() as session:
        task = session.get(AiTask, "card")
        assert task is not None
        task.daily_cap_eur = Decimal("0.01")
        session.commit()
    wire._handler = lambda request: httpx2.Response(
        200, json=_beta_message(model="claude-opus-5-20261001")
    )

    response = _wire_call(opener).complete(_request())

    assert response.model == "claude-opus-5-20261001"
    [row] = _usage_rows(opener)
    assert (row.model, row.priced) == ("claude-opus-5-20261001", True)
    assert row.cost_eur == Decimal("0.012000")
    with pytest.raises(LlmCapReached):
        _wire_call(opener).complete(_request())


def test_wire_outage_is_llm_unavailable_without_key_or_prompt_in_the_log(
    opener: SessionOpener, wire: Wire, caplog: pytest.LogCaptureFixture
) -> None:
    wire._handler = lambda request: httpx2.Response(
        500,
        json={"type": "error", "error": {"type": "api_error", "message": f"{API_KEY} {USER_TEXT}"}},
    )

    with caplog.at_level(logging.INFO), pytest.raises(LlmUnavailable) as error:
        _wire_call(opener).complete(_request())

    assert str(error.value) == UNAVAILABLE_SENTENCE
    assert API_KEY not in caplog.text
    assert USER_TEXT not in caplog.text
    # One retry, not the SDK's default two: the 90-second budget of the team builder.
    assert len(wire.requests) == 2


def test_wire_rate_limit_is_llm_unavailable(opener: SessionOpener, wire: Wire) -> None:
    wire._handler = lambda request: httpx2.Response(
        429,
        headers={"retry-after": "0"},
        json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
    )

    with pytest.raises(LlmUnavailable):
        _wire_call(opener).complete(_request())


# ---- RecordingCall ----------------------------------------------------------------------


def test_recording_call_answers_in_order_and_keeps_requests() -> None:
    first = LlmResponse(
        text="uno",
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
    )
    second = first.model_copy(update={"text": "due"})
    recording = RecordingCall([first, second])

    assert recording.complete(_request()) == first
    assert recording.complete(_request(max_tokens=10)) == second
    assert [request.max_tokens for request in recording.requests] == [None, 10]
