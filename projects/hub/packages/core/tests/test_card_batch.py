"""A regeneration through the Message Batches API (REB-853): the start that sends it, the
state that outlives a restart, and each result applied through the synchronous write's own
guard, rewrite and save.

The provider is a `RecordingBatch` (no network; `test_llm.py` drives the real one over a
mocked wire); the synchronous model, for the rewrite a guard asks for and the refusals a
batch cannot fall back on, is a `RecordingCall`; the database is the real one.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_cards import CARD, card_response
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_card_staleness import (
    _admin,
    _backdate,
    _cards,
)

from rebase_core.card_batch import SUBMITTING_GIVEN_UP_AFTER, BatchPolled, CardBatches, active_batch
from rebase_core.cards import PROMPT_VERSION, CardWriter
from rebase_core.errors import LlmCapReached, LlmUnavailable
from rebase_core.llm import BatchOutcome, RecordingBatch, RecordingCall
from rebase_core.models import (
    AdminAction,
    AiUsage,
    CardBatch,
    CardBatchItem,
    FreelancerCard,
    User,
)
from rebase_core.team_schemas import CardsRefreshed

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
NO_ANSWERS = RecordingCall([])
GENDERED = {**CARD, "sintesi": "Lei è una sviluppatrice con nove anni di esperienza."}


def _stale(session: Session, count: int = 3) -> list[UUID]:
    """`count` people with a card of prompt version 1: stale, listed for a start."""
    people = _cards(session, count)
    _backdate(session, 1)
    return people


def _outcomes(people: list[UUID], **special: BatchOutcome) -> dict[str, BatchOutcome]:
    """A good answer for everyone, but for the ids in `special` (keyed by their position)."""
    answers = {str(person): BatchOutcome("succeeded", card_response()) for person in people}
    for position, outcome in special.items():
        answers[str(people[int(position[1:])])] = outcome
    return answers


def _writer(
    session: Session, call: RecordingBatch, llm: object = NO_ANSWERS, threshold: int = 3
) -> CardWriter:
    return CardWriter(
        session,
        llm,  # type: ignore[arg-type]
        now=lambda: NOW,
        batches=CardBatches(call, threshold, now=lambda: NOW),
    )


def _an_admin(session: Session) -> UUID:
    found = session.scalar(select(User.id).where(User.role == "admin"))
    return found or _admin(session)


def _start(session: Session, call: RecordingBatch, limit: int = 50) -> CardsRefreshed:
    return _writer(session, call).regenerate_stale(_an_admin(session), limit)


def _poll(session: Session, call: RecordingBatch, llm: object = NO_ANSWERS) -> BatchPolled:
    return _writer(session, call, llm).batches.poll(_writer(session, call, llm))  # type: ignore[union-attr]


def _version(session: Session, person: UUID) -> int | None:
    session.expire_all()
    card = session.get(FreelancerCard, person)
    assert card is not None
    return card.prompt_version


def test_a_start_above_the_threshold_sends_one_batch_and_writes_nothing(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    call = RecordingBatch()

    result = _start(clean_hub, call)

    assert result == CardsRefreshed(written=0, failed=0, batched=3)
    assert call.submissions == 1
    assert sorted(call.submitted) == sorted(str(p) for p in people)
    [batch] = clean_hub.scalars(select(CardBatch)).all()
    assert (batch.status, batch.request_count, batch.provider_batch_id) == (
        "in_progress",
        3,
        "msgbatch_0001",
    )
    assert batch.submitted_at == NOW and batch.handle is not None
    items = clean_hub.scalars(select(CardBatchItem)).all()
    assert {i.freelancer_id for i in items} == set(people)
    assert {i.status for i in items} == {"pending"}
    # Nothing is written until the results are applied: the cards are the old ones.
    assert {_version(clean_hub, p) for p in people} == {1}
    assert active_batch(clean_hub) is not None and active_batch(clean_hub).count == 3  # type: ignore[union-attr]
    [action] = clean_hub.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate"))
    assert action.payload == {"limit": 50, "queued": 3, "batch": True}


def test_the_batch_requests_are_the_ones_the_synchronous_write_builds(clean_hub: Session) -> None:
    [person, *_] = _stale(clean_hub, 3)
    call = RecordingBatch()
    prepared = CardWriter(clean_hub, None).prepare(person)

    _start(clean_hub, call)

    assert call.submitted[str(person)] == prepared.request  # type: ignore[union-attr]
    item = clean_hub.get(CardBatchItem, (clean_hub.scalars(select(CardBatch.id)).one(), person))
    assert item is not None
    assert item.cv_sha256 == prepared.cv_sha256  # type: ignore[union-attr]
    assert item.input_hashes == prepared.hashes  # type: ignore[union-attr]


def test_below_the_threshold_the_workers_write_it(clean_hub: Session) -> None:
    _stale(clean_hub, 2)
    call = RecordingBatch()
    llm = RecordingCall([card_response(), card_response()])

    result = _writer(clean_hub, call, llm).regenerate_stale(_an_admin(clean_hub), 50)

    assert result == CardsRefreshed(written=2, failed=0)
    assert call.submissions == 0 and clean_hub.scalars(select(CardBatch)).all() == []
    [action] = clean_hub.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate"))
    assert action.payload == {"limit": 50, "queued": 2}


def test_a_second_start_while_a_batch_is_active_queues_nothing(clean_hub: Session) -> None:
    _stale(clean_hub, 3)
    call = RecordingBatch()
    _start(clean_hub, call)

    assert _writer(clean_hub, call).regeneration_running() is True
    assert _start(clean_hub, call) == CardsRefreshed(written=0, failed=0)
    assert call.submissions == 1
    assert len(clean_hub.scalars(select(AdminAction)).all()) == 1


def test_a_refused_submission_marks_the_batch_failed_and_leaves_every_card(
    clean_hub: Session,
) -> None:
    people = _stale(clean_hub, 3)
    call = RecordingBatch(refuse=LlmCapReached("Tetto giornaliero raggiunto"))

    result = _start(clean_hub, call)

    assert result == CardsRefreshed(written=0, failed=0, stopped=True)
    [batch] = clean_hub.scalars(select(CardBatch)).all()
    assert batch.status == "failed"
    assert _writer(clean_hub, call).regeneration_running() is False
    assert {c.freelancer_id for c in CardWriter(clean_hub, None).stale_cards()} == set(people)


def test_the_poll_waits_for_the_provider_then_applies_every_result(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    call = RecordingBatch(_outcomes(people), ended_after=1)
    _start(clean_hub, call)

    assert _poll(clean_hub, call) == BatchPolled("running", left=3)
    assert {_version(clean_hub, p) for p in people} == {1}

    polled = _poll(clean_hub, call)

    assert polled == BatchPolled("applied", written=3, failed=0)
    assert {_version(clean_hub, p) for p in people} == {PROMPT_VERSION}
    assert CardWriter(clean_hub, None).stale_cards() == []
    [batch] = clean_hub.scalars(select(CardBatch)).all()
    assert batch.status == "applied" and batch.ended_at == NOW and batch.applied_at == NOW
    assert {i.status for i in clean_hub.scalars(select(CardBatchItem))} == {"written"}
    # The usage of each answer was handed over once, for the batch price to be recorded.
    assert sorted(p for p, _ in call.recorded) == sorted(people)
    assert active_batch(clean_hub) is None
    assert _poll(clean_hub, call) == BatchPolled("none")


def test_a_card_that_gives_the_gender_away_is_rewritten_once_synchronously(
    clean_hub: Session,
) -> None:
    people = _stale(clean_hub, 3)
    gendered = BatchOutcome("succeeded", card_response(GENDERED))
    call = RecordingBatch(_outcomes(people, p1=gendered))
    _start(clean_hub, call)
    llm = RecordingCall([card_response()])  # the neutral rewrite

    polled = _poll(clean_hub, call, llm)

    assert polled == BatchPolled("applied", written=3, failed=0)
    [rewrite] = llm.requests
    assert [m.role for m in rewrite.messages] == ["user", "assistant", "user"]
    assert rewrite.messages[0] == call.submitted[str(people[1])].messages[0]
    card = clean_hub.get(FreelancerCard, people[1])
    assert card is not None and card.card is not None and "sviluppatrice" not in str(card.card)


def test_a_card_still_gendered_after_the_rewrite_is_failed_not_written(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    gendered = BatchOutcome("succeeded", card_response(GENDERED))
    call = RecordingBatch(_outcomes(people, p0=gendered))
    _start(clean_hub, call)
    llm = RecordingCall([card_response(GENDERED)])

    polled = _poll(clean_hub, call, llm)

    assert polled == BatchPolled("applied", written=2, failed=1)
    statuses = {i.freelancer_id: i.status for i in clean_hub.scalars(select(CardBatchItem))}
    assert statuses[people[0]] == "failed"
    clean_hub.expire_all()
    card = clean_hub.get(FreelancerCard, people[0])
    assert card is not None and card.error is not None and "genere" in card.error


def test_an_outage_on_a_rewrite_leaves_the_rest_for_the_next_pass_and_pays_once(
    clean_hub: Session,
) -> None:
    """The restart case: what was applied stays applied, what was not is read again from the
    provider and its usage, already recorded, is not recorded twice."""
    people = _stale(clean_hub, 3)
    gendered = BatchOutcome("succeeded", card_response(GENDERED))
    call = RecordingBatch(_outcomes(people, p0=gendered))
    _start(clean_hub, call)

    class Down:
        def complete(self, request: object) -> object:
            raise LlmUnavailable("giù")

    first = _poll(clean_hub, call, Down())

    # The two that need no model are applied all the same; the one that does waits.
    assert first == BatchPolled("applying", written=2, failed=0, left=1)
    assert _version(clean_hub, people[0]) == 1
    assert _writer(clean_hub, call).regeneration_running() is True

    # A new process: a new writer, the same tables, the model back.
    second = _poll(clean_hub, call, RecordingCall([card_response()]))

    assert second == BatchPolled("applied", written=1, failed=0)
    assert sorted(p for p, _ in call.recorded) == sorted(people)  # once each, not twice
    assert {_version(clean_hub, p) for p in people} == {PROMPT_VERSION}


def test_a_result_for_a_card_whose_inputs_changed_is_dropped_and_paid_for(
    clean_hub: Session,
) -> None:
    people = _stale(clean_hub, 3)
    call = RecordingBatch(_outcomes(people))
    _start(clean_hub, call)
    # The position is an input of the card: the answer was written for another one.
    clean_hub.execute(
        text("UPDATE freelancers SET posizione = 'Data engineer' WHERE id = :id"),
        {"id": people[0]},
    )
    clean_hub.commit()

    polled = _poll(clean_hub, call)

    assert polled == BatchPolled("applied", written=2, failed=0)
    statuses = {i.freelancer_id: i.status for i in clean_hub.scalars(select(CardBatchItem))}
    assert statuses[people[0]] == "superseded"
    assert _version(clean_hub, people[0]) == 1  # still the old card, still stale
    assert len(call.recorded) == 3  # the answer was paid for all the same


def test_a_request_the_provider_did_not_answer_leaves_its_card_stale(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    call = RecordingBatch(_outcomes(people, p0=BatchOutcome("errored"), p2=BatchOutcome("expired")))
    _start(clean_hub, call)

    polled = _poll(clean_hub, call)

    assert polled == BatchPolled("applied", written=1, failed=2)
    assert {c.freelancer_id for c in CardWriter(clean_hub, None).stale_cards()} == {
        people[0],
        people[2],
    }
    assert [p for p, _ in call.recorded] == [people[1]]  # no answer, nothing to pay


def test_a_result_missing_from_the_provider_counts_as_expired(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    answers = _outcomes(people)
    del answers[str(people[1])]
    call = RecordingBatch(answers)
    _start(clean_hub, call)

    assert _poll(clean_hub, call) == BatchPolled("applied", written=2, failed=1)


def test_a_refused_request_is_written_again_synchronously(clean_hub: Session) -> None:
    """A batch has no server-side fallback: a refusal is asked once more with the call that
    has it, and its card is whatever that answers."""
    people = _stale(clean_hub, 3)
    refused = BatchOutcome("succeeded", card_response(stop_reason="refusal"))
    call = RecordingBatch(_outcomes(people, p2=refused))
    _start(clean_hub, call)
    llm = RecordingCall([card_response()])

    polled = _poll(clean_hub, call, llm)

    assert polled == BatchPolled("applied", written=3, failed=0)
    assert len(llm.requests) == 1 and llm.requests[0] == call.submitted[str(people[2])]


def test_a_batch_never_sent_is_given_up_after_a_while(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    batch = CardBatch(
        provider="anthropic",
        status="submitting",
        request_count=3,
        created_at=NOW - timedelta(minutes=1),
    )
    clean_hub.add(batch)
    clean_hub.commit()
    call = RecordingBatch()

    young = _writer(clean_hub, call).batches.poll(_writer(clean_hub, call))  # type: ignore[union-attr]
    assert young.state == "running"

    clean_hub.execute(
        text("UPDATE card_batches SET created_at = :at"),
        {"at": NOW - SUBMITTING_GIVEN_UP_AFTER - timedelta(minutes=1)},
    )
    clean_hub.commit()
    clean_hub.expire_all()
    gone = _writer(clean_hub, call).batches.poll(_writer(clean_hub, call))  # type: ignore[union-attr]

    assert gone.state == "failed"
    clean_hub.refresh(batch)
    assert batch.status == "failed"
    assert {c.freelancer_id for c in CardWriter(clean_hub, None).stale_cards()} == set(people)


def test_without_a_batch_nothing_is_polled(clean_hub: Session) -> None:
    assert _poll(clean_hub, RecordingBatch()) == BatchPolled("none")


def test_a_card_written_without_a_model_configured_needs_no_model(clean_hub: Session) -> None:
    """Only a rewrite asks the model: with the card task off, the results that pass the guards
    are still applied, and the card of a paid-for answer does not carry an error sentence."""
    people = _stale(clean_hub, 3)
    call = RecordingBatch(_outcomes(people))
    _start(clean_hub, call)

    polled = _writer(clean_hub, call, None).batches.poll(_writer(clean_hub, call, None))  # type: ignore[union-attr]

    assert polled == BatchPolled("applied", written=3, failed=0)
    clean_hub.expire_all()
    assert {clean_hub.get(FreelancerCard, p).error for p in people} == {None}  # type: ignore[union-attr]


def test_an_answer_to_an_older_prompt_is_not_stamped_as_the_new_one(clean_hub: Session) -> None:
    """A deploy that bumps `PROMPT_VERSION` while the batch runs: the answer was written to
    the prompt of the version the item was built with, so the card stays as it was, stale."""
    people = _stale(clean_hub, 3)
    call = RecordingBatch(_outcomes(people))
    _start(clean_hub, call)
    clean_hub.execute(
        text("UPDATE card_batch_items SET prompt_version = :v"), {"v": PROMPT_VERSION - 1}
    )
    clean_hub.commit()

    polled = _poll(clean_hub, call)

    assert polled == BatchPolled("applied", written=0, failed=0)
    assert {i.status for i in clean_hub.scalars(select(CardBatchItem))} == {"superseded"}
    assert {_version(clean_hub, p) for p in people} == {1}
    assert len(call.recorded) == 3  # paid for all the same


def test_one_card_that_cannot_be_applied_does_not_hold_the_batch(
    clean_hub: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = _stale(clean_hub, 3)
    call = RecordingBatch(_outcomes(people))
    _start(clean_hub, call)
    real = CardWriter.store

    def broken(self: CardWriter, freelancer_id: UUID, digest: str, composed: object) -> object:
        if freelancer_id == people[1]:
            raise RuntimeError("a bug")
        return real(self, freelancer_id, digest, composed)  # type: ignore[arg-type]

    monkeypatch.setattr(CardWriter, "store", broken)

    polled = _poll(clean_hub, call)

    assert polled == BatchPolled("applied", written=2, failed=1)
    statuses = {i.freelancer_id: i.status for i in clean_hub.scalars(select(CardBatchItem))}
    assert statuses[people[1]] == "errored"
    assert _writer(clean_hub, call).regeneration_running() is False  # the next start is free


def test_the_cap_is_asked_about_the_batch_before_it_is_sent(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    # Three cards at 0.4 each as calls: 0.6 as a batch.
    clean_hub.add(
        AiUsage(
            task="card",
            purpose="run",
            provider="anthropic",
            model="m",
            priced=True,
            cost_eur=Decimal("0.4"),
        )
    )
    clean_hub.commit()
    call = RecordingBatch()

    _start(clean_hub, call)

    assert call.estimates == [Decimal("0.6000")]
    assert len(call.submitted) == len(people)
