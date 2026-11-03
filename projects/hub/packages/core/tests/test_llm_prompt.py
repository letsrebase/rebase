"""One helper builds a prompt's request and reads its answer (REB-892)."""

from uuid import uuid4

import pytest
from pydantic import BaseModel

from rebase_core.errors import LlmCapReached, LlmUnavailable
from rebase_core.llm import LlmRequest, LlmResponse
from rebase_core.llm_prompt import (
    complete_prompt,
    prompt_request,
    read_response,
    try_complete_request,
)
from rebase_core.llm_sentences import unavailable_sentence
from rebase_core.models import AI_TASKS


class Shape(BaseModel):
    word: str


def _response(
    parsed: BaseModel | None = None, stop_reason: str = "end_turn", category: str | None = None
) -> LlmResponse:
    return LlmResponse(
        text=None if stop_reason == "refusal" else "body",
        parsed=parsed,
        stop_reason=stop_reason,
        refusal_category=category,
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
    )


class _Call:
    def __init__(self, answer: LlmResponse | BaseException) -> None:
        self.answer = answer
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def test_the_request_is_the_rules_and_the_blocks_as_one_user_turn() -> None:
    person = uuid4()

    request = prompt_request(
        "rules", ["one", "two"], Shape, cached="catalogue", max_tokens=50, freelancer_id=person
    )

    assert request.system == "rules"
    assert request.system_cached == "catalogue"
    assert [(m.role, m.text) for m in request.messages] == [("user", "one\n\ntwo")]
    assert (request.output_model, request.max_tokens, request.freelancer_id) == (Shape, 50, person)


def test_an_answer_is_read_as_the_model_or_the_reason_there_is_none() -> None:
    assert read_response("card", _response(Shape(word="a")), Shape).answer == Shape(word="a")
    assert read_response("card", _response(stop_reason="refusal"), Shape).failure == "refusal"
    assert read_response("card", _response(Shape(word="a"), "max_tokens"), Shape).failure == (
        "max_tokens"
    )
    assert read_response("card", _response(None), Shape).failure == "shape"
    # Another model's body is not this one's shape.
    assert read_response("card", _response(LlmRequest.model_construct()), Shape).failure == "shape"


@pytest.mark.parametrize("task", AI_TASKS)
def test_a_failure_that_is_required_raises_the_tasks_own_sentence(task: str) -> None:
    done = read_response(task, _response(stop_reason="refusal", category="x"), Shape)

    with pytest.raises(LlmUnavailable) as raised:
        done.required("thing")

    assert raised.value.message == unavailable_sentence(task)


def test_only_the_team_builders_outage_speaks_of_a_team() -> None:
    sentences = {task: unavailable_sentence(task) for task in AI_TASKS}

    assert len(set(sentences.values())) == len(AI_TASKS)
    assert [task for task, sentence in sentences.items() if "team" in sentence] == ["team_proposal"]


def test_the_seams_refusals_reach_a_caller_that_asks_to_hear_them() -> None:
    llm = _Call(LlmCapReached("Tetto giornaliero raggiunto"))

    with pytest.raises(LlmCapReached):
        complete_prompt(llm, "card", "rules", ["x"], Shape)

    assert len(llm.requests) == 1


def test_a_caller_that_carries_on_without_the_model_gets_none_for_every_failure() -> None:
    request = prompt_request("rules", ["x"], Shape)
    failures: list[LlmResponse | BaseException] = [
        LlmCapReached("cap"),
        LlmUnavailable("down"),
        RuntimeError("the provider's library"),
        _response(stop_reason="refusal"),
        _response(None),
    ]

    for failure in failures:
        assert try_complete_request(_Call(failure), "crm_lead", request, Shape, label="t") is None
    assert try_complete_request(None, "crm_lead", request, Shape, label="t") is None
    answer = _response(Shape(word="a"))
    assert try_complete_request(_Call(answer), "crm_lead", request, Shape, label="t") == Shape(
        word="a"
    )
