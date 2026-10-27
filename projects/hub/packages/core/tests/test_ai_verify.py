"""`rebase ai-verify` (REB-840): what the report says for a stub model, with no network.
The live run against OpenAI and Google waits for a key an admin saves in «Modelli AI»."""

from typing import Any

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from rebase_core.ai_verify import PROMPT, VerifyReport, verify

KEY = "sk-live-not-a-real-key-1234"
GOOD = '{"city": "Paris", "country": "France"}'


def _factory(*answers: ModelResponse | Exception, built: list[Any] | None = None) -> Any:
    queue = list(answers)

    def factory(
        provider: str, model: str, api_key: str, effort: str | None
    ) -> tuple[Model, ModelSettings]:
        if built is not None:
            built.append((provider, model, api_key, effort))

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            answer = queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return FunctionModel(respond, model_name=model), ModelSettings()

    return factory


def _reply(body: str = GOOD) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=body)],
        usage=RequestUsage(input_tokens=20, output_tokens=8),
    )


def test_report_flags_each_capability_separately() -> None:
    built: list[Any] = []
    ok = verify("google", "m", KEY, model_factory=_factory(_reply(), _reply(), built=built))
    assert ok == VerifyReport(True, True, True, True, None)
    assert ok.ok
    assert [b[3] for b in built] == [None, "low"]  # the plain call, then the effort setting

    not_native = verify("openai", "m", KEY, model_factory=_factory(_reply("sure!"), _reply()))
    assert (not_native.key_injected, not_native.native_output_ok) == (True, False)
    assert not_native.thinking_ok and not not_native.ok

    no_effort = verify(
        "google",
        "m",
        KEY,
        model_factory=_factory(_reply(), ModelHTTPError(400, "m", "bad thinking level")),
    )
    assert (no_effort.native_output_ok, no_effort.thinking_ok) == (True, False)
    assert not no_effort.ok


def test_a_rejected_key_is_not_injected_and_a_rejected_schema_is() -> None:
    rejected = verify(
        "openai", "m", KEY, model_factory=_factory(ModelHTTPError(401, "m", "bad key"))
    )
    assert (rejected.key_injected, rejected.native_output_ok) == (False, False)

    schema = verify("google", "m", KEY, model_factory=_factory(ModelHTTPError(400, "m", "schema")))
    assert (schema.key_injected, schema.native_output_ok) == (True, False)


def test_report_error_has_no_key() -> None:
    body = f"Incorrect API key provided: {KEY}"
    report = verify("openai", "m", KEY, model_factory=_factory(ModelHTTPError(401, "m", body)))

    printed = "\n".join(report.lines())
    assert KEY not in printed
    assert "status 401" in printed
    assert KEY[:10] not in printed


def test_the_prompt_is_fixed_and_carries_no_personal_data() -> None:
    assert "@" not in PROMPT and "CV" not in PROMPT
