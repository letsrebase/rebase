"""The trail of a regeneration (REB-861): who started it and by which door, each card it
wrote or failed to write in that talent's own trail, and the end of a bulk one with what it
cost. «tracciate in tutto e per tutto»: the worker path, the Message Batch path, a single
«Rigenera scheda», and the writes nobody started, which record nothing.

The model is a scripted seam that, as the real one does, writes one priced `ai_usage` row for
the person the call is about (`LlmRequest.freelancer_id`); the batch is a `RecordingBatch` that
records its usage at half that, as `AiBatch.record` does. The database is the real one.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import MODEL, card_response
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_card_batch import GENDERED, _an_admin, _outcomes, _poll, _stale, _start
from test_card_staleness import _apply, _cards

from rebase_core.ai_config import AI_CONFIG_ENTITY, AI_CONFIG_ENTITY_ID
from rebase_core.card_batch import ACTIVE_GIVEN_UP_AFTER, BatchPolled, CardBatches
from rebase_core.cards import (
    PROMPT_VERSION,
    REGENERATION_REASONS,
    CardWriter,
    Fonte,
    Regeneration,
)
from rebase_core.db import session_factory
from rebase_core.errors import LlmCapReached, LlmUnavailable
from rebase_core.llm import BatchHandle, BatchOutcome, LlmRequest, LlmResponse, RecordingBatch
from rebase_core.models import AdminAction, AiUsage, CardBatch
from rebase_core.team_schemas import CardsRefreshed

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
CALL_COST = Decimal("0.010000")
BATCH_COST = Decimal("0.005000")
GENDERED_CARD = {
    "ruolo": "Backend developer",
    "seniority": "senior",
    "anni": 9,
    "competenze": ["Python"],
    "settori": ["fintech"],
    "lingue": ["italiano"],
    "luogo": "Torino",
    "sintesi": "Lei è una sviluppatrice con nove anni di esperienza.",
}


class PricedCall:
    """A model that answers with `answer(request)` and writes the priced usage row the real
    seam writes, for the person the request names."""

    def __init__(
        self, engine: Engine, answer: Callable[[LlmRequest], LlmResponse] | None = None
    ) -> None:
        self._engine = engine
        self._answer = answer or (lambda request: card_response())

    def complete(self, request: LlmRequest) -> LlmResponse:
        response = self._answer(request)
        with Session(self._engine) as session:
            session.add(
                AiUsage(
                    task="card",
                    purpose="run",
                    provider="anthropic",
                    model=MODEL,
                    cost_eur=CALL_COST,
                    priced=True,
                    freelancer_id=request.freelancer_id,
                )
            )
            session.commit()
        return response


class PricedBatch(RecordingBatch):
    """`RecordingBatch` that records each answer's usage on the caller's session, uncommitted,
    at the batch price."""

    def record(
        self, session: Session, handle: BatchHandle, response: LlmResponse, freelancer_id: UUID
    ) -> None:
        super().record(session, handle, response, freelancer_id)
        session.add(
            AiUsage(
                task="card",
                purpose="run",
                provider="anthropic",
                model=MODEL,
                cost_eur=BATCH_COST,
                priced=True,
                freelancer_id=freelancer_id,
                batch=True,
            )
        )


class Down:
    def complete(self, request: object) -> object:
        raise LlmUnavailable("giù")


def _engine(session: Session) -> Engine:
    bind = session.get_bind()
    assert isinstance(bind, Engine)
    return bind


def _actions(session: Session, kind: str, entity_id: UUID | None = None) -> list[AdminAction]:
    session.expire_all()
    query = select(AdminAction).where(AdminAction.kind == kind)
    if entity_id is not None:
        query = query.where(AdminAction.entity_id == entity_id)
    return list(session.scalars(query.order_by(AdminAction.created_at, AdminAction.id)))


def _one(session: Session, kind: str, entity_id: UUID | None = None) -> AdminAction:
    [action] = _actions(session, kind, entity_id)
    return action


def _cost(payload: dict[str, Any]) -> Decimal:
    return Decimal(payload["cost_eur"])


def _no_personal_data(*actions: AdminAction) -> None:
    text = json.dumps([a.payload for a in actions])
    assert "Persona" not in text and "Cognome" not in text and "Backend" not in text


# ---- the worker path -------------------------------------------------------------------


@pytest.mark.parametrize(("fonte", "workers"), [("web", 1), ("cli", 3)])
def test_a_start_is_signed_and_sourced_and_its_end_counts_the_cards_and_the_cost(
    clean_hub: Session, fonte: Fonte, workers: int
) -> None:
    people = _stale(clean_hub, 4)
    admin = _an_admin(clean_hub)
    refused = {people[0]: card_response(stop_reason="refusal")}
    llm = PricedCall(
        _engine(clean_hub),
        lambda request: refused.get(request.freelancer_id, card_response()),  # type: ignore[arg-type]
    )

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=workers).regenerate_stale(
        admin, 50, fonte=fonte
    )

    assert result == CardsRefreshed(written=3, failed=1)
    start = _one(clean_hub, "cards_regenerate")
    assert start.entity_type == AI_CONFIG_ENTITY and start.entity_id == AI_CONFIG_ENTITY_ID
    assert start.admin_id == admin
    assert start.payload == {"limit": 50, "queued": 4, "fonte": fonte, "batch": False}
    end = _one(clean_hub, "cards_regenerated")
    assert end.entity_type == AI_CONFIG_ENTITY and end.admin_id == admin
    p = end.payload
    assert p["fonte"] == fonte and p["start_action_id"] == str(start.id)
    assert (p["written"], p["failed"], p["superseded"], p["skipped"], p["errored"]) == (
        3,
        1,
        0,
        0,
        0,
    )
    assert p["failures"] == [{"freelancer_id": str(people[0]), "reason": "refusal"}]
    assert _cost(p) == 4 * CALL_COST
    assert "stopped" not in p and "batch_id" not in p
    _no_personal_data(start, end)


def test_each_card_a_start_rewrites_is_in_its_talents_trail(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    admin = _an_admin(clean_hub)
    refused = {people[1]: card_response(stop_reason="refusal")}
    llm = PricedCall(
        _engine(clean_hub),
        lambda request: refused.get(request.freelancer_id, card_response()),  # type: ignore[arg-type]
    )

    CardWriter(clean_hub, llm, now=lambda: NOW, workers=2).regenerate_stale(admin, 50, fonte="cli")

    start = _one(clean_hub, "cards_regenerate")
    for person in people:
        action = _one(clean_hub, "card_regenerated", person)
        assert (action.entity_type, action.admin_id) == ("freelancer", admin)
        p = action.payload
        assert (p["fonte"], p["bulk"], p["start_action_id"]) == ("cli", True, str(start.id))
        assert (p["prompt_version"], p["model"]) == (PROMPT_VERSION, MODEL)
        assert _cost(p) == CALL_COST
        if person == people[1]:
            assert (p["esito"], p["reason"]) == ("non_scritta", "refusal")
        else:
            assert p["esito"] == "scritta" and "reason" not in p
        _no_personal_data(action)


def test_an_outage_ends_the_regeneration_stopped_with_the_people_never_started(
    clean_hub: Session,
) -> None:
    _stale(clean_hub, 5)
    admin = _an_admin(clean_hub)

    result = CardWriter(clean_hub, Down(), now=lambda: NOW).regenerate_stale(  # type: ignore[arg-type]
        admin, 50, fonte="web"
    )

    assert result.stopped is True
    p = _one(clean_hub, "cards_regenerated").payload
    assert (p["written"], p["failed"], p["skipped"], p["left"], p["stopped"]) == (0, 1, 0, 4, True)
    assert [f["reason"] for f in p["failures"]] == ["unavailable"]
    assert len(_actions(clean_hub, "card_regenerated")) == 1


def test_a_card_that_is_no_longer_stale_when_its_turn_comes_is_skipped_not_recorded(
    clean_hub: Session,
) -> None:
    people = _stale(clean_hub, 3)
    admin = _an_admin(clean_hub)
    second = CardWriter(clean_hub, None).stale_cards()[1].freelancer_id

    def another_start_wrote_it() -> None:
        other = session_factory(_engine(clean_hub))()
        try:
            llm = PricedCall(_engine(clean_hub))
            CardWriter(other, llm, now=lambda: NOW).write(second, force=True)  # type: ignore[arg-type]
        finally:
            other.close()

    class Hooked(PricedCall):
        calls = 0

        def complete(self, request: LlmRequest) -> LlmResponse:
            Hooked.calls += 1
            if Hooked.calls == 1:
                another_start_wrote_it()
            return super().complete(request)

    llm = Hooked(_engine(clean_hub))
    result = CardWriter(clean_hub, llm, now=lambda: NOW).regenerate_stale(  # type: ignore[arg-type]
        admin, 50, fonte="web"
    )

    assert result == CardsRefreshed(written=2, failed=0)
    p = _one(clean_hub, "cards_regenerated").payload
    assert (p["written"], p["failed"], p["skipped"]) == (2, 0, 1)
    # The skipped card is not this regeneration's: nothing of it in its talent's trail, and
    # the write that got there first was nobody's start.
    assert _actions(clean_hub, "card_regenerated", second) == []
    assert {a.entity_id for a in _actions(clean_hub, "card_regenerated")} == set(people) - {second}
    assert _cost(p) == 2 * CALL_COST


def test_a_start_with_nothing_stale_is_still_signed_and_ended(clean_hub: Session) -> None:
    _cards(clean_hub, 2)
    admin = _an_admin(clean_hub)

    CardWriter(clean_hub, PricedCall(_engine(clean_hub)), now=lambda: NOW).regenerate_stale(  # type: ignore[arg-type]
        admin, 50, fonte="cli"
    )

    assert _one(clean_hub, "cards_regenerate").payload["queued"] == 0
    p = _one(clean_hub, "cards_regenerated").payload
    assert (p["written"], p["failed"], p["skipped"], p["failures"]) == (0, 0, 0, [])
    assert _cost(p) == 0


def test_without_a_model_a_start_records_nothing(clean_hub: Session) -> None:
    _stale(clean_hub, 2)

    CardWriter(clean_hub, None).regenerate_stale(_an_admin(clean_hub), 50, fonte="web")

    assert _actions(clean_hub, "cards_regenerate") == []
    assert _actions(clean_hub, "cards_regenerated") == []


# ---- a single «Rigenera scheda», and the writes nobody started -------------------------


@pytest.mark.parametrize("fonte", ["web", "mcp"])
def test_a_single_regeneration_is_signed_in_the_talents_trail(
    clean_hub: Session, fonte: Fonte
) -> None:
    [person] = _cards(clean_hub, 1)
    admin = _an_admin(clean_hub)
    llm = PricedCall(_engine(clean_hub))

    CardWriter(clean_hub, llm, now=lambda: NOW, run=Regeneration(admin, fonte)).write(  # type: ignore[arg-type]
        person, force=True
    )

    action = _one(clean_hub, "card_regenerated", person)
    assert action.admin_id == admin
    p = action.payload
    assert (p["fonte"], p["bulk"], p["esito"]) == (fonte, False, "scritta")
    assert "start_action_id" not in p
    assert (p["prompt_version"], p["model"], _cost(p)) == (PROMPT_VERSION, MODEL, CALL_COST)
    assert _actions(clean_hub, "cards_regenerate") == []
    assert _actions(clean_hub, "cards_regenerated") == []


def test_a_single_regeneration_that_fails_says_why_and_what_it_cost(clean_hub: Session) -> None:
    [person] = _cards(clean_hub, 1)
    llm = PricedCall(_engine(clean_hub), lambda request: card_response(GENDERED_CARD))

    CardWriter(
        clean_hub,
        llm,
        now=lambda: NOW,
        run=Regeneration(_an_admin(clean_hub), "web"),  # type: ignore[arg-type]
    ).write(person, force=True)

    p = _one(clean_hub, "card_regenerated", person).payload
    assert (p["esito"], p["reason"]) == ("non_scritta", "gendered")
    # The first call and the one rewrite it asks for.
    assert _cost(p) == 2 * CALL_COST


def test_an_outage_on_a_single_regeneration_is_recorded_as_such(clean_hub: Session) -> None:
    [person] = _cards(clean_hub, 1)

    CardWriter(
        clean_hub,
        Down(),
        now=lambda: NOW,
        run=Regeneration(_an_admin(clean_hub), "mcp"),  # type: ignore[arg-type]
    ).write(person, force=True)

    p = _one(clean_hub, "card_regenerated", person).payload
    assert (p["esito"], p["reason"], _cost(p)) == ("non_scritta", "unavailable", 0)


def test_the_hourly_refresh_and_a_write_nobody_started_record_nothing(clean_hub: Session) -> None:
    person = _apply(clean_hub, 1)
    llm = PricedCall(_engine(clean_hub))

    assert CardWriter(clean_hub, llm, now=lambda: NOW).refresh_stale() == CardsRefreshed(1, 0)  # type: ignore[arg-type]
    CardWriter(clean_hub, llm, now=lambda: NOW).write(person, force=True)  # type: ignore[arg-type]

    assert _actions(clean_hub, "card_regenerated") == []
    assert _actions(clean_hub, "cards_regenerate") == []
    assert _actions(clean_hub, "cards_regenerated") == []


# ---- the Message Batch path ------------------------------------------------------------


def test_a_batch_records_its_start_with_the_batch_and_its_end_when_it_is_applied(
    clean_hub: Session,
) -> None:
    people = _stale(clean_hub, 5)
    admin = _an_admin(clean_hub)
    answers = _outcomes(
        people,
        p0=BatchOutcome("succeeded", card_response(GENDERED)),  # still gendered after its rewrite
        p1=BatchOutcome("errored"),
    )
    call = PricedBatch(answers, ended_after=1)
    llm = PricedCall(_engine(clean_hub), lambda request: card_response(GENDERED))

    _start(clean_hub, call)

    [batch] = clean_hub.scalars(select(CardBatch)).all()
    start = _one(clean_hub, "cards_regenerate")
    assert start.admin_id == admin
    assert start.payload == {
        "limit": 50,
        "queued": 5,
        "fonte": "web",
        "batch": True,
        "batch_id": str(batch.id),
    }
    # The position is an input of the card: the answer for the third was written for another.
    clean_hub.execute(
        text("UPDATE freelancers SET posizione = 'Data engineer' WHERE id = :id"),
        {"id": people[2]},
    )
    clean_hub.commit()

    assert _poll(clean_hub, call, llm).state == "running"
    # Nothing ends while the batch runs.
    assert _actions(clean_hub, "cards_regenerated") == []
    assert _actions(clean_hub, "card_regenerated") == []

    polled = _poll(clean_hub, call, llm)

    assert polled == BatchPolled("applied", written=2, failed=2)
    end = _one(clean_hub, "cards_regenerated")
    assert end.admin_id == admin
    p = end.payload
    assert (p["fonte"], p["start_action_id"]) == ("web", str(start.id))
    assert (p["batch_id"], p["batch_status"]) == (str(batch.id), "applied")
    assert (p["written"], p["failed"], p["superseded"], p["errored"], p["skipped"]) == (
        2,
        1,
        1,
        1,
        0,
    )
    assert sorted((f["freelancer_id"], f["reason"]) for f in p["failures"]) == sorted(
        [
            (str(people[0]), "gendered"),
            (str(people[1]), "errored"),
            (str(people[2]), "superseded"),
        ]
    )
    # The batch price of four answers (the errored one has none) and the rewrite that failed.
    assert _cost(p) == 4 * BATCH_COST + CALL_COST
    assert "stopped" not in p
    _no_personal_data(start, end)

    expected = {
        people[0]: ("non_scritta", "gendered", BATCH_COST + CALL_COST),
        people[1]: ("non_scritta", "errored", Decimal(0)),
        people[2]: ("non_scritta", "superseded", BATCH_COST),
        people[3]: ("scritta", None, BATCH_COST),
        people[4]: ("scritta", None, BATCH_COST),
    }
    for person, (esito, reason, cost) in expected.items():
        card = _one(clean_hub, "card_regenerated", person)
        assert card.admin_id == admin
        q = card.payload
        assert (q["fonte"], q["bulk"], q["start_action_id"]) == ("web", True, str(start.id))
        assert (q["esito"], q.get("reason")) == (esito, reason)
        assert _cost(q) == cost
    # Applied once: a later pass adds nothing.
    assert _poll(clean_hub, call, llm) == BatchPolled("none")
    assert len(_actions(clean_hub, "cards_regenerated")) == 1


def test_a_card_waiting_for_the_model_is_recorded_once_it_is_settled(clean_hub: Session) -> None:
    """The restart case: the rewrite a card needs finds the model down, so the card waits;
    nothing is signed for it until it is settled, and its cost counts the batch's price and the
    rewrite, one end for the whole."""
    people = _stale(clean_hub, 3)
    call = PricedBatch(_outcomes(people, p0=BatchOutcome("succeeded", card_response(GENDERED))))
    _start(clean_hub, call)

    first = _poll(clean_hub, call, Down())

    assert first == BatchPolled("applying", written=2, failed=0, left=1)
    assert _actions(clean_hub, "card_regenerated", people[0]) == []
    assert _actions(clean_hub, "cards_regenerated") == []

    second = _poll(clean_hub, call, PricedCall(_engine(clean_hub)))

    assert second == BatchPolled("applied", written=1, failed=0)
    waited = _one(clean_hub, "card_regenerated", people[0]).payload
    assert waited["esito"] == "scritta" and _cost(waited) == BATCH_COST + CALL_COST
    end = _one(clean_hub, "cards_regenerated").payload
    assert (end["written"], end["failed"]) == (3, 0)
    assert _cost(end) == 3 * BATCH_COST + CALL_COST


def test_a_refused_submission_ends_the_regeneration_failed(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    call = PricedBatch(refuse=LlmCapReached("Tetto giornaliero raggiunto"))

    result = _start(clean_hub, call)

    assert result == CardsRefreshed(written=0, failed=0, stopped=True)
    [batch] = clean_hub.scalars(select(CardBatch)).all()
    start = _one(clean_hub, "cards_regenerate")
    assert start.payload["batch_id"] == str(batch.id)
    p = _one(clean_hub, "cards_regenerated").payload
    assert (p["batch_status"], p["stopped"], p["written"], p["errored"]) == ("failed", True, 0, 3)
    assert {f["reason"] for f in p["failures"]} == {"not_sent"}
    assert {f["freelancer_id"] for f in p["failures"]} == {str(x) for x in people}
    for person in people:
        assert _one(clean_hub, "card_regenerated", person).payload["reason"] == "not_sent"


def test_a_batch_given_up_after_two_days_ends_failed_with_the_cards_left_stale(
    clean_hub: Session,
) -> None:
    people = _stale(clean_hub, 3)
    call = PricedBatch(_outcomes(people), ended_after=10_000)
    _start(clean_hub, call)
    later = NOW + ACTIVE_GIVEN_UP_AFTER + timedelta(minutes=1)
    writer = CardWriter(
        clean_hub, None, now=lambda: later, batches=CardBatches(call, 3, now=lambda: later)
    )

    polled = writer.batches.poll(writer)  # type: ignore[union-attr]

    assert polled == BatchPolled("failed", failed=3)
    p = _one(clean_hub, "cards_regenerated").payload
    assert (p["batch_status"], p["errored"], p["written"]) == ("failed", 3, 0)
    assert {f["reason"] for f in p["failures"]} == {"expired"}
    assert {a.entity_id for a in _actions(clean_hub, "card_regenerated")} == set(people)


def test_a_batch_without_a_start_in_the_trail_applies_and_records_nothing(
    clean_hub: Session,
) -> None:
    """One sent before REB-861 has no start naming its id: it is carried on as it always was."""
    people = _stale(clean_hub, 3)
    call = PricedBatch(_outcomes(people))
    _start(clean_hub, call)
    clean_hub.execute(text("DELETE FROM admin_actions"))
    clean_hub.commit()

    polled = _poll(clean_hub, call)

    assert polled == BatchPolled("applied", written=3, failed=0)
    assert _actions(clean_hub, "card_regenerated") == []
    assert _actions(clean_hub, "cards_regenerated") == []


def test_the_batch_is_signed_by_the_admin_who_started_it_whoever_polls(clean_hub: Session) -> None:
    people = _stale(clean_hub, 3)
    admin = _an_admin(clean_hub)
    call = PricedBatch(_outcomes(people))
    _start(clean_hub, call)

    # The poll is the `cards` service's: no admin, no door, and a writer of its own.
    _poll(clean_hub, call)

    assert {a.admin_id for a in _actions(clean_hub, "card_regenerated")} == {admin}
    assert _one(clean_hub, "cards_regenerated").admin_id == admin


def test_unrelated_synchronous_spend_on_a_person_never_lands_in_the_batch_cards_cost(
    clean_hub: Session,
) -> None:
    """A wizard or CV write, an hourly refresh or a «Rigenera scheda» on the same person while the
    batch is open is its own spend, and its own entry when a person started it: the batch card's
    cost and the end's total are the batch's answers alone."""
    people = _stale(clean_hub, 3)
    call = PricedBatch(_outcomes(people), ended_after=1)
    _start(clean_hub, call)
    for person in people[:2]:
        clean_hub.add(
            AiUsage(
                task="card",
                purpose="run",
                provider="anthropic",
                model=MODEL,
                cost_eur=Decimal("0.500000"),
                priced=True,
                freelancer_id=person,
            )
        )
    clean_hub.commit()

    assert _poll(clean_hub, call).state == "running"
    assert _poll(clean_hub, call) == BatchPolled("applied", written=3, failed=0)

    for person in people:
        assert _cost(_one(clean_hub, "card_regenerated", person).payload) == BATCH_COST
    assert _cost(_one(clean_hub, "cards_regenerated").payload) == 3 * BATCH_COST


def test_a_database_error_mid_run_still_records_the_end_and_raises_the_original(
    clean_hub: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error leaves the session's transaction aborted: the end is recorded after a rollback,
    and the caller gets the database's error, not the one the aborted transaction would raise."""
    people = _stale(clean_hub, 3)
    admin = _an_admin(clean_hub)

    def broken(self: CardWriter, ids: Any, **kwargs: Any) -> None:
        kwargs["outcomes"].update(dict.fromkeys(ids))
        self.session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(CardWriter, "_write_all", broken)

    with pytest.raises(DBAPIError, match="division by zero"):
        CardWriter(clean_hub, PricedCall(_engine(clean_hub)), now=lambda: NOW).regenerate_stale(  # type: ignore[arg-type]
            admin, 50, fonte="web"
        )

    p = _one(clean_hub, "cards_regenerated").payload
    assert (p["stopped"], p["left"], p["written"], p["skipped"]) == (True, 3, 0, 0)
    assert p["start_action_id"] == str(_one(clean_hub, "cards_regenerate").id)
    assert len(people) == 3


def test_every_reason_the_backend_can_record_has_a_label_in_the_web() -> None:
    notes = Path(__file__).resolve().parents[3] / "apps/web/src/lib/notes.ts"
    block = re.search(r"REGENERATION_REASON_LABELS[^{]*\{(.*?)\n\}", notes.read_text(), re.DOTALL)
    assert block is not None
    labelled = set(re.findall(r"^\s+(\w+):", block.group(1), re.MULTILINE))

    assert set(REGENERATION_REASONS) <= labelled
    assert labelled <= set(REGENERATION_REASONS)
