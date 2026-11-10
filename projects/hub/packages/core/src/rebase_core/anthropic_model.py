"""Pydantic AI's Anthropic model, taught the one thing the hub's call needs that it does
not know yet: the server-side fallback's `fallback` content block (REB-831).

With the `server-side-fallback-2026-07-01` beta and `fallbacks="default"` on the request
(`llm.build_model`), Anthropic retries a refused turn on a substitute model and marks the
point where the first model's output gives way to the second's with a `fallback` block in
`content`. Pydantic AI's `_process_response` has a branch for every block type it knows and
an `assert` after the last one, so a response carrying that block would raise an
`AssertionError` instead of reaching the card writer: the exact case the fallback exists
for. This subclass removes everything up to and including the last `fallback` block before
the parent reads the message, so what the caller sees is what the model that finally
answered wrote, which is the only part that is the answer (a model that declined may have
left a partial one before the block).

It overrides a private method of an exactly pinned version (`pydantic-ai-slim==…` in
`packages/core/pyproject.toml`); `test_llm.py` drives a real `BetaMessage` carrying the
block through it, so an upgrade that moves the method fails there, not in production.
"""

from collections.abc import Sequence
from typing import Any

from anthropic.types.beta import BetaMessage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.settings import ModelSettings


class ServerFallbackAnthropicModel(AnthropicModel):
    def _process_response(
        self,
        response: BetaMessage,
        model_request_parameters: ModelRequestParameters,
        model_settings: ModelSettings,
        history_fetched_pages: Sequence[tuple[str, str]] = (),
    ) -> Any:
        last_fallback = max(
            (index for index, block in enumerate(response.content) if block.type == "fallback"),
            default=None,
        )
        if last_fallback is not None:
            response = response.model_copy(
                update={"content": response.content[last_fallback + 1 :]}
            )
        return super()._process_response(
            response,
            model_request_parameters,
            model_settings,  # type: ignore[arg-type]
            history_fetched_pages,
        )
