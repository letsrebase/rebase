"""The hub's one seam onto Claude (REB-508): two future callers -- the anonymous card
(spec § 5.1) and the team proposal (spec § 3.4) -- share `LlmCall` and never see the
`anthropic` SDK itself. Like `EmailSender` in `mail.py`, a protocol, one real
implementation on the official SDK, and `call_from_settings` answering `None` without a
key so a caller refuses with its own sentence rather than guessing at one.

`AnthropicCall` never raises past `complete`: a `LlmUnavailable` is the only thing that
crosses the boundary, carrying the one sentence a page can show as it stands, because
the SDK's own exceptions carry no Italian and no promise to keep meaning the same thing
after the next version. Nothing here logs the request: not the CV text a card is
written from, not a project's description, not the key.
"""

import logging
from typing import Any, Protocol, cast

import anthropic
from pydantic import BaseModel

from rebase_core.config import Settings

logger = logging.getLogger(__name__)

# The one sentence a provider outage shows on a page (global-constraints.md).
UNAVAILABLE_SENTENCE = "Non riesco a proporre un team adesso: riprova tra poco."

# The server-side fallback opt-in, exactly as the spec's § 5 and the global constraints
# name it: never the older array form, never a client-side fallback list.
_BETAS = ["server-side-fallback-2026-07-01"]


class LlmRequest(BaseModel):
    """`system` and `messages` are already `client.beta.messages.create`'s own wire
    shape (text blocks; the last of `system` may carry `cache_control`), left as plain
    dicts so this file is the only one that reads the SDK's own types."""

    system: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    schema: dict[str, Any]  # type: ignore[assignment]  # `output_config.format`'s `json_schema`
    max_tokens: int


class LlmResponse(BaseModel):
    text: str | None  # the first text block; `None` on a refusal
    stop_reason: str
    refusal_category: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


class LlmUnavailable(Exception):
    """The provider did not answer: the sentence the page shows."""


class LlmCall(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...


class AnthropicCall:
    """`client` is a keyword-only override: production leaves it out and gets
    `anthropic.Anthropic(api_key=...)`; the tests hand a stub that records
    `beta.messages.create`'s kwargs and answers a canned response, no network."""

    def __init__(
        self, api_key: str, model: str, *, client: anthropic.Anthropic | None = None
    ) -> None:
        self.client = client if client is not None else anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, request: LlmRequest) -> LlmResponse:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=request.max_tokens,
                betas=_BETAS,
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": request.schema},
                },
                # `LlmRequest.system`/`.messages` are already the SDK's own wire shape;
                # this cast is the one place in the hub that trusts it, so the two
                # future callers never import an `anthropic` type of their own.
                system=cast(Any, request.system),
                messages=cast(Any, request.messages),
            )
        except anthropic.APIConnectionError as error:
            logger.warning("Anthropic %s: connection error", type(error).__name__)
            raise LlmUnavailable(UNAVAILABLE_SENTENCE) from error
        except anthropic.RateLimitError as error:
            logger.warning("Anthropic %s: status %s", type(error).__name__, error.status_code)
            raise LlmUnavailable(UNAVAILABLE_SENTENCE) from error
        except anthropic.APIStatusError as error:
            logger.warning("Anthropic %s: status %s", type(error).__name__, error.status_code)
            raise LlmUnavailable(UNAVAILABLE_SENTENCE) from error

        if response.stop_reason == "refusal":
            text = None
            refusal_category = response.stop_details.category if response.stop_details else None
        else:
            text = next((block.text for block in response.content if block.type == "text"), None)
            refusal_category = None

        return LlmResponse(
            text=text,
            stop_reason=response.stop_reason or "",
            refusal_category=refusal_category,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
        )


def call_from_settings(settings: Settings) -> LlmCall | None:
    """`None` without a key: the callers then refuse with their own sentence."""
    if not settings.anthropic_api_key:
        return None
    return AnthropicCall(settings.anthropic_api_key, settings.team_builder_model)
