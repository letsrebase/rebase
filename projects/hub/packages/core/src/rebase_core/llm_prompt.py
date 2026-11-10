"""The one place a prompt becomes an `LlmRequest` and an answer becomes a verdict (REB-892).

Before this, the card writer, the card judge, the team builder and the CRM draft each
assembled their own `LlmRequest` and each read `stop_reason` and `parsed` their own way. The
seam (`llm.py`, `LlmCall.complete`) and its exceptions are unchanged: this sits on top of it.

- `prompt_request` assembles: the rules, the data blocks as one user turn, the answer's
  model, an optional cached block, a caller's output ceiling and the person the call is about.
- `read_response` reads one answer: the parsed model, or why there is none (`FailureKind`).
- `complete_request` is the call and the reading, for a request built elsewhere (the card
  writer's and the CRM draft's, which another route sends too); `complete_prompt` is
  assembly plus that. Both let whatever the seam raises (`LlmUnavailable` and its kin: a cap
  reached, no model configured) go to the caller, which is what a caller that has to tell
  its user (the team builder, the card judge) wants.
- `try_complete_request` is the same for a caller that must carry on without the model (the
  CRM draft: the request is filed with no draft): every failure, the seam's or any other,
  is `None`, logged under `label` with the exception's class and never its text.

What the caller does with a `Completion` is its own policy: `Completion.required` turns a
failure into `LlmUnavailable` with the task's own sentence (`llm_sentences`); the judge
reads `failure` itself; the card writer needs the `response` for its tokens and its
rewrite. Nothing here logs a prompt, a CV or an answer.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from rebase_core.errors import LlmUnavailable
from rebase_core.llm import LlmCall, LlmMessage, LlmRequest, LlmResponse
from rebase_core.llm_sentences import unavailable_sentence

logger = logging.getLogger(__name__)

# Why a call that was answered has no usable body: the model declined, the answer was cut at
# the ceiling, or the body is not the shape asked for. A provider that could not answer at all
# is not one of these: it is the seam's `LlmUnavailable`.
FailureKind = Literal["refusal", "max_tokens", "shape"]


def prompt_request(
    system: str,
    blocks: Sequence[str],
    output_model: type[BaseModel],
    *,
    cached: str | None = None,
    max_tokens: int | None = None,
    freelancer_id: UUID | None = None,
) -> LlmRequest:
    """The request: `system` the rules, `blocks` joined by a blank line as the one user turn,
    `output_model` the shape of the answer (the place every limit is enforced). `cached` is
    text every call repeats, sent as a second system block and marked for caching where the
    provider has it; `max_tokens` a ceiling of the caller's own below the task's;
    `freelancer_id` the person on the usage row, never sent to a provider."""
    return LlmRequest(
        system=system,
        system_cached=cached,
        messages=[LlmMessage(role="user", text="\n\n".join(blocks))],
        output_model=output_model,
        max_tokens=max_tokens,
        freelancer_id=freelancer_id,
    )


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    """One answered call: the `response` as the seam handed it over, and its `result`, the
    body validated as the asked model or the `FailureKind` that says why there is none."""

    task: str
    response: LlmResponse
    result: T | FailureKind

    @property
    def answer(self) -> T | None:
        return None if isinstance(self.result, str) else self.result

    @property
    def failure(self) -> FailureKind | None:
        return self.result if isinstance(self.result, str) else None

    def required(self, label: str) -> T:
        """The answer, or `LlmUnavailable` with the task's own sentence. The log says which
        failure (and for a refusal its category), never the body; the exception carries no
        cause, whose text could quote the answer."""
        if not isinstance(self.result, str):
            return self.result
        if self.result == "refusal":
            logger.warning(
                "%s not written: refusal (category %s)", label, self.response.refusal_category
            )
        elif self.result == "max_tokens":
            logger.warning("%s not written: max_tokens", label)
        else:
            logger.warning("%s not written: the answer is not the shape", label)
        raise LlmUnavailable(unavailable_sentence(self.task))


def read_response[T: BaseModel](
    task: str, response: LlmResponse, output_model: type[T]
) -> Completion[T]:
    """`stop_reason` is read before the body, as the seam hands it over: a refusal and a cut
    answer have no answer whatever `parsed` holds; otherwise `parsed` is the answer when the
    seam validated it as `output_model`, and the body is not the shape when it is not."""
    if response.stop_reason == "refusal":
        return Completion(task, response, "refusal")
    if response.stop_reason == "max_tokens":
        return Completion(task, response, "max_tokens")
    if not isinstance(response.parsed, output_model):
        return Completion(task, response, "shape")
    return Completion(task, response, response.parsed)


def complete_request[T: BaseModel](
    llm: LlmCall, task: str, request: LlmRequest, output_model: type[T]
) -> Completion[T]:
    """The call and `read_response`. The seam's own refusals (`LlmUnavailable`,
    `LlmCapReached`, `LlmNotConfigured`) are not caught: they reach the caller untouched."""
    return read_response(task, llm.complete(request), output_model)


def complete_prompt[T: BaseModel](
    llm: LlmCall,
    task: str,
    system: str,
    blocks: Sequence[str],
    output_model: type[T],
    *,
    cached: str | None = None,
    max_tokens: int | None = None,
    freelancer_id: UUID | None = None,
) -> Completion[T]:
    """`prompt_request` then `complete_request`."""
    request = prompt_request(
        system,
        blocks,
        output_model,
        cached=cached,
        max_tokens=max_tokens,
        freelancer_id=freelancer_id,
    )
    return complete_request(llm, task, request, output_model)


def try_complete_request[T: BaseModel](
    llm: LlmCall | None,
    task: str,
    request: LlmRequest,
    output_model: type[T],
    *,
    label: str,
) -> T | None:
    """The answer, or `None` for every way there is none: no model (`llm` is `None`), the
    seam refusing or failing (a non-`LlmUnavailable` error included: a caller that carries
    on without the model must not be stopped by the provider's library), a refusal, a cut
    answer, a body that is not the shape. Logged under `label` at info for the outage and the
    answer's failure, at warning for an unexpected error, and only by class or kind."""
    if llm is None:
        return None
    try:
        done = complete_request(llm, task, request, output_model)
    except LlmUnavailable as error:
        logger.info("%s: no answer (%s)", label, error.code)
        return None
    except Exception as error:
        logger.warning("%s: no answer (%s)", label, type(error).__name__)
        return None
    if done.answer is None:
        logger.info("%s: no answer (%s)", label, done.response.stop_reason)
    return done.answer
