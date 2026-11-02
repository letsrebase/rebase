"""Cards written by a pool of workers (REB-853): `refresh_stale` and `regenerate_stale` with
`workers` above one. Each worker is a session and a writer of its own, each person is
claimed (an advisory lock for the whole write) so no card is paid for twice, and the first
outage stops the pool with the rest left as it was. One worker is the old sequential
writer, which `test_cards.py` and `test_card_staleness.py` hold.

The model is a scripted seam that holds its first calls until the workers are in them
together (a worker is in its call while the others claim) and counts what it was asked;
the database is the real one, because the claim is a Postgres lock.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest
from admins import an_admin
from fakes_cards import card_response
from freelancer_fixtures import apply_numbered, backdate_prompt, written_cards
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.card_prompt import PROMPT_VERSION
from rebase_core.card_writer import CardWriter
from rebase_core.errors import LlmCapReached, LlmUnavailable
from rebase_core.llm import LlmRequest, LlmResponse
from rebase_core.models import AdminAction, FreelancerCard
from rebase_core.team_schemas import CardsRefreshed

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
MEET_TIMEOUT = 10.0


class PooledCall:
    """A model that answers with `answer(request)` and keeps every request, under a lock,
    since the workers ask from threads. With `parties`, the first calls wait until that
    many are in flight at once (an event, not a sleep): the pool is then proven to run its
    workers together, and a worker is inside its call while the others claim their people.
    If they never meet, the call fails after `MEET_TIMEOUT` seconds instead of passing."""

    def __init__(
        self,
        answer: Callable[[LlmRequest], LlmResponse] | None = None,
        *,
        parties: int = 1,
    ) -> None:
        self._answer = answer or (lambda request: card_response())
        self._parties = parties
        self._met = threading.Event()
        self._lock = threading.Lock()
        self.requests: list[LlmRequest] = []
        self.in_flight = 0
        self.most_in_flight = 0

    def complete(self, request: LlmRequest) -> LlmResponse:
        with self._lock:
            self.requests.append(request)
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
            if self.in_flight >= self._parties:
                self._met.set()
        try:
            if not self._met.wait(MEET_TIMEOUT):
                raise AssertionError(f"{self._parties} calls were never in flight together")
            return self._answer(request)
        finally:
            with self._lock:
                self.in_flight -= 1


def _who(request: LlmRequest) -> str:
    """The person a card prompt is about: `apply_numbered` writes their number into the CV."""
    text = request.messages[0].text
    return text.split("Persona", 1)[1].split(",", 1)[0]


def _new_cv_people(session: Session, count: int) -> list[UUID]:
    return [apply_numbered(session, n) for n in range(1, count + 1)]


def test_a_pool_writes_each_person_once_and_runs_them_at_once(clean_hub: Session) -> None:
    people = _new_cv_people(clean_hub, 6)
    llm = PooledCall(parties=3)

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=3).refresh_stale(limit=50)

    assert result == CardsRefreshed(written=6, failed=0)
    # One call per person, none twice, and more than one in flight at a time.
    assert sorted(_who(r) for r in llm.requests) == ["1", "2", "3", "4", "5", "6"]
    assert llm.most_in_flight > 1
    cards = clean_hub.scalars(select(FreelancerCard).where(FreelancerCard.card.is_not(None))).all()
    assert {c.freelancer_id for c in cards} == set(people)
    assert {c.prompt_version for c in cards} == {PROMPT_VERSION}


def test_two_workers_never_write_the_same_person(clean_hub: Session) -> None:
    """The list names each person twice, so two workers reach the same card together: the
    claim lets one write it and the other finds it taken, or already written, and pays
    nothing."""
    first, second = _new_cv_people(clean_hub, 2)
    llm = PooledCall(parties=2)

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=4)._write_all(
        [first, first, second, second], force=False, only_stale=False
    )

    assert result == CardsRefreshed(written=2, failed=0)
    assert len(llm.requests) == 2
    assert sorted(_who(r) for r in llm.requests) == ["1", "2"]


def test_a_regeneration_checks_the_card_is_still_stale_under_the_claim(clean_hub: Session) -> None:
    """`force` rewrites whatever the CV's hash says, so only the staleness stops the
    second claimant of a card the first has just written."""
    people = written_cards(clean_hub, 2)
    backdate_prompt(clean_hub, 1)
    llm = PooledCall(parties=2)

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=4)._write_all(
        [people[0], people[0], people[1], people[1]], force=True, only_stale=True
    )

    assert result == CardsRefreshed(written=2, failed=0)
    assert len(llm.requests) == 2


def test_the_first_outage_stops_the_pool_and_leaves_the_rest(clean_hub: Session) -> None:
    _new_cv_people(clean_hub, 10)

    def down(request: LlmRequest) -> LlmResponse:
        raise LlmUnavailable("giù")

    llm = PooledCall(down, parties=2)

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=2).refresh_stale(limit=50)

    assert result.stopped is True and result.written == 0
    # Only the calls already in flight were made: the rest were never started, and the
    # people who were not asked have no failure recorded, so the next run asks them.
    assert 1 <= len(llm.requests) <= 2
    assert result.failed == len(llm.requests)
    untouched = clean_hub.scalars(select(FreelancerCard)).all()
    assert len(untouched) == len(llm.requests)


def test_a_spent_cap_stops_the_pool_like_an_outage(clean_hub: Session) -> None:
    people = written_cards(clean_hub, 6)
    backdate_prompt(clean_hub, 1)

    def spent(request: LlmRequest) -> LlmResponse:
        raise LlmCapReached("Tetto giornaliero raggiunto")

    llm = PooledCall(spent, parties=2)

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=2).regenerate_stale(
        an_admin(clean_hub), 6, fonte="web"
    )

    assert result.stopped is True and result.written == 0
    assert len(llm.requests) <= 2
    # The cards are still the old ones, still stale: nothing was retired.
    assert {c.freelancer_id for c in CardWriter(clean_hub, None).stale_cards()} == set(people)


def test_the_counts_add_up_when_some_cards_fail(clean_hub: Session) -> None:
    _new_cv_people(clean_hub, 8)

    def answer(request: LlmRequest) -> LlmResponse:
        # A refusal for the odd ones: a failure of the CV's own, not an outage.
        if int(_who(request)) % 2:
            return card_response(stop_reason="refusal")
        return card_response()

    llm = PooledCall(answer)

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=4).refresh_stale(limit=50)

    assert result == CardsRefreshed(written=4, failed=4)
    assert len(llm.requests) == 8
    # Failed on their own account: not asked again until the CV changes.
    again = PooledCall()
    assert CardWriter(
        clean_hub, again, now=lambda: NOW, workers=4
    ).refresh_stale() == CardsRefreshed(0, 0)
    assert again.requests == []


def test_regenerate_stale_with_workers_records_one_action_and_writes_each_once(
    clean_hub: Session,
) -> None:
    written_cards(clean_hub, 5)
    backdate_prompt(clean_hub, 1)
    admin = an_admin(clean_hub)
    llm = PooledCall(parties=2)

    result = CardWriter(clean_hub, llm, now=lambda: NOW, workers=3).regenerate_stale(
        admin, 4, fonte="web"
    )

    assert result == CardsRefreshed(written=4, failed=0)
    assert len(llm.requests) == 4 and llm.most_in_flight > 1
    assert len(CardWriter(clean_hub, None).stale_cards()) == 1
    [action] = clean_hub.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate"))
    assert action.payload == {"limit": 4, "queued": 4, "fonte": "web", "batch": False}


def test_a_worker_that_raises_ends_the_pool_and_raises_after_it(clean_hub: Session) -> None:
    _new_cv_people(clean_hub, 6)

    def broken(request: LlmRequest) -> LlmResponse:
        raise RuntimeError("a bug")

    llm = PooledCall(broken, parties=2)

    with pytest.raises(RuntimeError, match="a bug"):
        CardWriter(clean_hub, llm, now=lambda: NOW, workers=2).refresh_stale(limit=50)

    assert len(llm.requests) <= 2
